import { useState, useRef, useCallback, useEffect } from 'react';
import Recorder from 'opus-recorder';
import encoderPath from 'opus-recorder/dist/encoderWorker.min.js?url';

/**
 * PersonaPlex full-duplex voice hook.
 * Connects to the bridge proxy (ws://localhost:8999/api/chat) which proxies
 * to the PersonaPlex server. Handles mic capture (Opus encoding),
 * audio playback (Opus decoding via WASM worker + AudioWorklet),
 * and text token display.
 *
 * v2 additions:
 * - Auto-reconnect on unexpected disconnect (max 3 attempts, 3s delay)
 * - Intentional disconnect flag to prevent reconnect on user-initiated close
 *
 * Protocol (matches reference client exactly):
 *   0x00 = handshake (server → client, triggers recording start)
 *   0x01 = audio (bidirectional, Opus Ogg pages)
 *   0x02 = text (server → client, UTF-8 tokens)
 *   0x03 = control (bidirectional)
 *   0x06 = ping
 */

const BRIDGE_URL = 'ws://localhost:8999/api/chat';
const DECODER_SAMPLE_RATE = 24000;
const ENCODER_SAMPLE_RATE = 24000;
const MAX_RECONNECT_ATTEMPTS = 3;
const RECONNECT_DELAY_MS = 3000;

// Minimal valid Ogg BOS page with OpusHead header (mono, 48kHz)
// Triggers the decoder worker's internal init to create buffers
function createWarmupBosPage() {
    const opusHead = new Uint8Array([
        0x4F, 0x70, 0x75, 0x73, 0x48, 0x65, 0x61, 0x64,
        0x01, 0x01, 0x38, 0x01,
        0x80, 0xBB, 0x00, 0x00,
        0x00, 0x00, 0x00,
    ]);
    const pageHeader = new Uint8Array([
        0x4F, 0x67, 0x67, 0x53,
        0x00, 0x02,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x01, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00,
        0x01, 0x13,
    ]);
    const bosPage = new Uint8Array(pageHeader.length + opusHead.length);
    bosPage.set(pageHeader, 0);
    bosPage.set(opusHead, pageHeader.length);
    return bosPage;
}

export function usePersonaPlex() {
    const [voiceStatus, setVoiceStatus] = useState('disconnected');
    const [personaplexText, setPersonaplexText] = useState('');
    const [isVoiceActive, setIsVoiceActive] = useState(false);

    const wsRef = useRef(null);
    const audioCtxRef = useRef(null);
    const workletRef = useRef(null);
    const decoderWorkerRef = useRef(null);
    const recorderRef = useRef(null);
    const micDurationRef = useRef(0);
    const cleaningUpRef = useRef(false);
    const handshakeReceivedRef = useRef(false);
    const decoderReadyRef = useRef(false);
    const handshakeTimeoutRef = useRef(null);
    const reconnectAttemptsRef = useRef(0);
    const reconnectTimerRef = useRef(null);
    const intentionalDisconnectRef = useRef(false);

    // --- Decode incoming audio (server → speakers) ---
    const decodeAudio = useCallback((data) => {
        if (!decoderWorkerRef.current) return;
        decoderWorkerRef.current.postMessage(
            { command: 'decode', pages: data },
            [data.buffer]
        );
    }, []);

    // --- Handle decoded PCM from worker → AudioWorklet ---
    const onDecoderMessage = useCallback((e) => {
        if (!e.data || !workletRef.current) return;
        const pcmFrame = e.data[0]; // Float32Array from decoder
        workletRef.current.port.postMessage({
            frame: pcmFrame,
            type: 'audio',
            micDuration: micDurationRef.current,
        });
    }, []);

    // --- Start mic recording (called only after handshake + decoder ready) ---
    const startRecording = useCallback((audioCtx) => {
        if (recorderRef.current) return;

        // Match reference client's recorder config exactly
        const recorderOptions = {
            encoderPath,
            bufferLength: Math.round(960 * audioCtx.sampleRate / ENCODER_SAMPLE_RATE),
            encoderFrameSize: 20,
            encoderSampleRate: ENCODER_SAMPLE_RATE,
            maxFramesPerPage: 2,
            numberOfChannels: 1,
            recordingGain: 1,
            resampleQuality: 3,
            encoderComplexity: 0,
            encoderApplication: 2049, // VOIP
            streamPages: true,
            mediaTrackConstraints: {
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                },
            },
        };

        const recorder = new Recorder(recorderOptions);
        let chunkIdx = 0;

        recorder.ondataavailable = (data) => {
            // data is Uint8Array (one Ogg page) when streamPages: true
            micDurationRef.current = recorder.encodedSamplePosition / 48000;

            if (chunkIdx < 5) {
                console.log('[PersonaPlex] Mic chunk', chunkIdx++, 'size:', data.length, 'dur:', micDurationRef.current.toFixed(3));
            }

            if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
                // Prepend 0x01 kind byte — send Uint8Array (not .buffer)
                const frame = new Uint8Array(1 + data.length);
                frame[0] = 0x01;
                frame.set(data, 1);
                wsRef.current.send(frame);
            }
        };

        recorder.onstart = () => {
            console.log('[PersonaPlex] Mic recording started');
            setIsVoiceActive(true);
        };

        recorder.onstop = () => {
            console.log('[PersonaPlex] Mic recording stopped');
            setIsVoiceActive(false);
            recorderRef.current = null;
        };

        try {
            recorder.start();
            recorderRef.current = recorder;
        } catch (err) {
            console.error('[PersonaPlex] Failed to start recorder (mic permission?):', err);
            setVoiceStatus('disconnected');
        }
    }, []);

    const stopRecording = useCallback(() => {
        if (recorderRef.current) {
            recorderRef.current.stop();
        }
    }, []);

    // --- Try to start recording once both handshake + decoder are ready ---
    const maybeStartRecording = useCallback(() => {
        if (handshakeReceivedRef.current && decoderReadyRef.current && audioCtxRef.current && !recorderRef.current) {
            console.log('[PersonaPlex] Handshake + decoder ready → starting mic');
            if (workletRef.current) {
                workletRef.current.port.postMessage({ type: 'reset' });
            }
            startRecording(audioCtxRef.current);
        }
    }, [startRecording]);

    // --- Handle WebSocket binary messages ---
    const handleBinaryMessage = useCallback((data) => {
        const arr = new Uint8Array(data);
        if (arr.length === 0) return;

        const kind = arr[0];
        const payload = arr.slice(1);

        switch (kind) {
            case 0x00: // Handshake — server is ready
                console.log('[PersonaPlex] Handshake received — server ready');
                handshakeReceivedRef.current = true;
                reconnectAttemptsRef.current = 0; // Reset on successful handshake
                if (handshakeTimeoutRef.current) {
                    clearTimeout(handshakeTimeoutRef.current);
                    handshakeTimeoutRef.current = null;
                }
                setVoiceStatus('connected');
                maybeStartRecording();
                break;
            case 0x01: // Audio from server
                decodeAudio(payload);
                break;
            case 0x02: { // Text token
                const text = new TextDecoder().decode(payload);
                setPersonaplexText(prev => prev + text);
                break;
            }
            case 0x03: // Control
                console.log('[PersonaPlex] Control:', payload[0]);
                break;
            case 0x05: { // Error
                const errText = new TextDecoder().decode(payload);
                console.error('[PersonaPlex] Server error:', errText);
                break;
            }
            default:
                break;
        }
    }, [decodeAudio, maybeStartRecording]);

    // --- Cleanup (internal, does NOT trigger reconnect) ---
    const cleanupInternal = useCallback(() => {
        cleaningUpRef.current = true;
        handshakeReceivedRef.current = false;
        decoderReadyRef.current = false;

        if (handshakeTimeoutRef.current) {
            clearTimeout(handshakeTimeoutRef.current);
            handshakeTimeoutRef.current = null;
        }

        stopRecording();

        if (wsRef.current) {
            wsRef.current.onclose = null;
            wsRef.current.onmessage = null;
            wsRef.current.onerror = null;
            if (wsRef.current.readyState === WebSocket.OPEN ||
                wsRef.current.readyState === WebSocket.CONNECTING) {
                wsRef.current.close();
            }
            wsRef.current = null;
        }

        if (decoderWorkerRef.current) {
            decoderWorkerRef.current.terminate();
            decoderWorkerRef.current = null;
        }

        if (workletRef.current) {
            workletRef.current.disconnect();
            workletRef.current = null;
        }

        if (audioCtxRef.current) {
            audioCtxRef.current.close().catch(() => {});
            audioCtxRef.current = null;
        }

        setIsVoiceActive(false);
        cleaningUpRef.current = false;
    }, [stopRecording]);

    // --- Connect ---
    const connectRef = useRef(null);

    const connect = useCallback(async () => {
        if (wsRef.current) return;
        if (cleaningUpRef.current) return;

        intentionalDisconnectRef.current = false;
        setVoiceStatus('connecting');
        setPersonaplexText('');
        handshakeReceivedRef.current = false;
        decoderReadyRef.current = false;

        try {
            // 1. Create AudioContext (48kHz to match Opus internal rate)
            const audioCtx = new AudioContext({ sampleRate: 48000 });
            audioCtxRef.current = audioCtx;

            // 2. Register AudioWorklet processor
            await audioCtx.audioWorklet.addModule('/assets/audio-processor.js');
            const worklet = new AudioWorkletNode(audioCtx, 'moshi-processor');
            worklet.connect(audioCtx.destination);
            workletRef.current = worklet;

            // 3. Create decoder worker + init
            const worker = new Worker('/assets/decoderWorker.min.js');
            worker.onmessage = onDecoderMessage;
            worker.onerror = (e) => console.error('[PersonaPlex] Decoder worker error:', e.message);

            worker.postMessage({
                command: 'init',
                bufferLength: 960 * audioCtx.sampleRate / DECODER_SAMPLE_RATE,
                decoderSampleRate: DECODER_SAMPLE_RATE,
                outputBufferSampleRate: audioCtx.sampleRate,
                resampleQuality: 0,
            });

            // Warmup BOS page (matches reference client)
            setTimeout(() => {
                console.log('[PersonaPlex] Sending warmup BOS page');
                worker.postMessage({
                    command: 'decode',
                    pages: createWarmupBosPage(),
                });
            }, 100);

            decoderWorkerRef.current = worker;

            // 4. Wait for decoder WASM to init (1s, same as reference)
            await new Promise(resolve => setTimeout(resolve, 1000));
            decoderReadyRef.current = true;
            console.log('[PersonaPlex] Decoder ready');

            // 5. Open WebSocket to bridge proxy
            const params = new URLSearchParams({
                voice_prompt: 'NATM0.pt',
                text_prompt: 'You are JARVIS, a highly capable AI assistant created by Tony Stark. You speak with a refined British accent, are knowledgeable, witty, and efficient. Be concise and helpful.',
            });
            const ws = new WebSocket(`${BRIDGE_URL}?${params}`);
            ws.binaryType = 'arraybuffer';

            ws.onopen = () => {
                console.log('[PersonaPlex] WebSocket opened, waiting for handshake...');
                // Timeout: if no handshake within 15s, give up
                handshakeTimeoutRef.current = setTimeout(() => {
                    if (!handshakeReceivedRef.current) {
                        console.error('[PersonaPlex] Handshake timeout — server did not respond in 15s');
                        cleanupInternal();
                        setVoiceStatus('disconnected');
                    }
                }, 15000);
            };

            ws.onmessage = (event) => {
                handleBinaryMessage(event.data);
            };

            ws.onclose = (event) => {
                console.log('[PersonaPlex] WebSocket closed:', event.code, event.reason);
                if (!cleaningUpRef.current) {
                    cleanupInternal();

                    // Auto-reconnect if not intentional and under retry limit
                    if (!intentionalDisconnectRef.current &&
                        reconnectAttemptsRef.current < MAX_RECONNECT_ATTEMPTS) {
                        reconnectAttemptsRef.current += 1;
                        const attempt = reconnectAttemptsRef.current;
                        console.log(`[PersonaPlex] Auto-reconnecting (attempt ${attempt}/${MAX_RECONNECT_ATTEMPTS})...`);
                        setVoiceStatus('reconnecting');

                        reconnectTimerRef.current = setTimeout(() => {
                            if (connectRef.current && !intentionalDisconnectRef.current) {
                                connectRef.current();
                            }
                        }, RECONNECT_DELAY_MS);
                    } else {
                        setVoiceStatus('disconnected');
                    }
                }
            };

            ws.onerror = (err) => {
                console.error('[PersonaPlex] WebSocket error:', err);
            };

            wsRef.current = ws;

            // If handshake already arrived (unlikely but possible), start recording
            maybeStartRecording();

        } catch (err) {
            console.error('[PersonaPlex] Connection failed:', err);
            setVoiceStatus('disconnected');
            cleanupInternal();
        }
    }, [handleBinaryMessage, onDecoderMessage, startRecording, maybeStartRecording, cleanupInternal]);

    // Keep connectRef in sync so reconnect timer can call latest connect
    connectRef.current = connect;

    // --- Public disconnect (intentional, no reconnect) ---
    const disconnect = useCallback(() => {
        intentionalDisconnectRef.current = true;
        reconnectAttemptsRef.current = 0;
        if (reconnectTimerRef.current) {
            clearTimeout(reconnectTimerRef.current);
            reconnectTimerRef.current = null;
        }
        cleanupInternal();
        setVoiceStatus('disconnected');
    }, [cleanupInternal]);

    const clearText = useCallback(() => {
        setPersonaplexText('');
    }, []);

    useEffect(() => {
        return () => {
            intentionalDisconnectRef.current = true;
            if (reconnectTimerRef.current) {
                clearTimeout(reconnectTimerRef.current);
            }
            cleanupInternal();
        };
    }, [cleanupInternal]);

    return {
        voiceStatus,
        personaplexText,
        isVoiceActive,
        connect,
        disconnect,
        clearText,
    };
}
