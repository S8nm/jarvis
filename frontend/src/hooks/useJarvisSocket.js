import { useState, useEffect, useRef, useCallback } from 'react';

const WS_URL = 'ws://127.0.0.1:8765/ws';
const API_URL = 'http://127.0.0.1:8765';
const RECONNECT_DELAY = 3000;

/**
 * React hook for managing WebSocket connection to the Jarvis backend.
 * Handles reconnection, message parsing, state management, tool events, and dashboard data.
 */
export function useJarvisSocket() {
    const [connected, setConnected] = useState(false);
    const [agentState, setAgentState] = useState('IDLE');
    const [conversation, setConversation] = useState([]);
    const [transcript, setTranscript] = useState({ text: '', confidence: 0 });
    const [streamingText, setStreamingText] = useState('');
    const [isStreaming, setIsStreaming] = useState(false);
    const [status, setStatus] = useState({});
    const [dashboard, setDashboard] = useState({
        notes: { total: 0, pinned: 0, tags: [], recent: [] },
        calendar: { today_count: 0, today_events: [], upcoming_count: 0, upcoming_events: [], total: 0, calendars: [] },
        camera_active: false,
        scripts: 0
    });
    const [toolActivity, setToolActivity] = useState(null); // Current tool being executed
    const [detections, setDetections] = useState([]); // Object detection results
    const [audioLevel, setAudioLevel] = useState({ rms: 0, isSpeech: false }); // Live mic level
    const [queueSize, setQueueSize] = useState(0); // Pending text input queue size
    const [piStatus, setPiStatus] = useState(null); // Pi worker / PicoClaw status
    const [routeInfo, setRouteInfo] = useState(null); // Last route decision from backend
    const [piHealth, setPiHealth] = useState(null); // Live Pi health from monitor

    const wsRef = useRef(null);
    const reconnectTimer = useRef(null);
    const toolClearTimer = useRef(null);
    const isStreamingRef = useRef(false);

    const connect = useCallback(() => {
        if (wsRef.current?.readyState === WebSocket.OPEN) return;

        const ws = new WebSocket(WS_URL);

        ws.onopen = () => {
            console.log('[JARVIS] WebSocket connected');
            setConnected(true);
            // Fetch initial dashboard data
            fetchDashboard();
        };

        ws.onclose = () => {
            console.log('[JARVIS] WebSocket disconnected');
            setConnected(false);
            reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY);
        };

        ws.onerror = (err) => {
            console.error('[JARVIS] WebSocket error:', err);
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                handleMessageRef.current(msg);
            } catch (e) {
                console.warn('[JARVIS] Failed to parse message:', e);
            }
        };

        wsRef.current = ws;
    }, []);

    const fetchDashboard = useCallback(async () => {
        try {
            const resp = await fetch(`${API_URL}/dashboard`);
            if (resp.ok) {
                const json = await resp.json();
                if (json.status === 'ok' && json.data) {
                    setDashboard(json.data);
                }
            }
        } catch (e) {
            console.warn('[JARVIS] Dashboard fetch failed:', e);
        }
    }, []);

    const fetchStatus = useCallback(async () => {
        try {
            const resp = await fetch(`${API_URL}/health`);
            if (resp.ok) {
                const json = await resp.json();
                if (json.status === 'online' && json.agent) {
                    setStatus(json.agent);
                    if (json.agent.state) setAgentState(json.agent.state);
                }
            }
        } catch (e) {
            console.warn('[JARVIS] Status fetch failed:', e);
        }
    }, []);

    const fetchPiStatus = useCallback(async () => {
        try {
            const resp = await fetch(`${API_URL}/pi/status`);
            if (resp.ok) {
                const json = await resp.json();
                if (json.status === 'ok' && json.data) {
                    setPiStatus(json.data);
                }
            }
        } catch (e) {
            // Silent — Pi may not be configured
        }
    }, []);

    const handleMessageRef = useRef(null);

    const handleMessage = useCallback((msg) => {
        const { type, data } = msg;

        switch (type) {
            case 'init':
                setStatus(data);
                if (data.conversation) setConversation(data.conversation);
                if (data.state) setAgentState(data.state);
                if (data.dashboard) setDashboard(data.dashboard);
                break;

            case 'state_change':
                setAgentState(data.state);
                if (data.state === 'THINKING') {
                    setStreamingText('');
                    setIsStreaming(true);
                    isStreamingRef.current = true;
                }
                if (data.state === 'EXECUTING') {
                    setIsStreaming(true);
                    isStreamingRef.current = true;
                }
                if (data.state === 'IDLE') {
                    setIsStreaming(false);
                    isStreamingRef.current = false;
                    setToolActivity(null);
                    setAudioLevel({ rms: 0, isSpeech: false });
                }
                break;

            case 'transcript':
                setTranscript({
                    text: data.text,
                    confidence: data.confidence,
                    language: data.language,
                    duration: data.duration,
                });
                break;

            case 'listening_started':
                setTranscript({ text: '', confidence: 0 });
                setAudioLevel({ rms: 0, isSpeech: false });
                break;

            case 'audio_level':
                setAudioLevel({ rms: data.rms, isSpeech: data.is_speech });
                break;

            case 'response_chunk':
                setStreamingText(prev => prev + data.token);
                if (!isStreamingRef.current) {
                    setIsStreaming(true);
                }
                break;

            case 'personaplex_status':
                // Voice session state from bridge
                console.log('[JARVIS] PersonaPlex:', data.status);
                break;

            case 'response_clear':
                // Clear streaming text for fresh summary after tool execution
                setStreamingText('');
                break;

            case 'response_complete':
                setIsStreaming(false);
                isStreamingRef.current = false;
                setStreamingText('');
                if (data.conversation) {
                    setConversation(data.conversation);
                }
                break;

            case 'tool_executing':
                // Clear any pending auto-clear timer from a previous tool
                if (toolClearTimer.current) {
                    clearTimeout(toolClearTimer.current);
                    toolClearTimer.current = null;
                }
                setToolActivity({
                    tool: data.tool,
                    args: data.args,
                    status: 'executing'
                });
                break;

            case 'tool_result':
                setToolActivity({
                    tool: data.tool,
                    result: data.result,
                    status: 'completed'
                });
                // Clear previous timer before setting new one
                if (toolClearTimer.current) {
                    clearTimeout(toolClearTimer.current);
                }
                toolClearTimer.current = setTimeout(() => {
                    setToolActivity(null);
                    toolClearTimer.current = null;
                }, 3000);
                break;

            case 'dashboard_update':
                setDashboard(data);
                break;

            case 'detection_result':
                setDetections(data);
                break;

            case 'history_cleared':
                setConversation([]);
                setTranscript({ text: '', confidence: 0 });
                setStreamingText('');
                break;

            case 'input_queued':
                setQueueSize(data.queue_size || 0);
                break;

            case 'mic_calibrated':
                console.log('[JARVIS] Mic calibrated, threshold:', data.threshold);
                break;

            case 'route_decision':
                setRouteInfo({
                    target: data.target,
                    intentType: data.intent_type,
                    confidence: data.confidence,
                    reason: data.reason,
                    classificationMs: data.classification_ms,
                    toolHint: data.tool_hint,
                });
                break;

            case 'pi_health':
                setPiHealth(data);
                break;

            case 'rate_limited':
                console.warn('[JARVIS] Rate limited:', data);
                break;

            case 'error':
                console.error('[JARVIS] Agent error:', data.message);
                break;

            case 'pong':
                break;

            default:
                console.log('[JARVIS] Unknown message type:', type, data);
        }
    }, []);

    handleMessageRef.current = handleMessage;

    // Connect on mount
    useEffect(() => {
        connect();
        return () => {
            clearTimeout(reconnectTimer.current);
            clearTimeout(toolClearTimer.current);
            wsRef.current?.close();
        };
    }, [connect]);

    // Ping keepalive
    useEffect(() => {
        const interval = setInterval(() => {
            if (wsRef.current?.readyState === WebSocket.OPEN) {
                wsRef.current.send(JSON.stringify({ type: 'ping' }));
            }
        }, 30000);
        return () => clearInterval(interval);
    }, []);

    // Refresh dashboard periodically (every 60s)
    useEffect(() => {
        const interval = setInterval(fetchDashboard, 60000);
        return () => clearInterval(interval);
    }, [fetchDashboard]);

    // Refresh status frequently for live system metrics (every 2s)
    useEffect(() => {
        const interval = setInterval(fetchStatus, 2000);
        return () => clearInterval(interval);
    }, [fetchStatus]);

    // Refresh Pi status periodically (every 15s)
    useEffect(() => {
        fetchPiStatus();
        const interval = setInterval(fetchPiStatus, 15000);
        return () => clearInterval(interval);
    }, [fetchPiStatus]);

    const sendMessage = useCallback((type, data = {}) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({ type, data }));
        }
    }, []);

    const sendText = useCallback((text) => {
        sendMessage('text_input', { text });
    }, [sendMessage]);

    const triggerVoice = useCallback(() => {
        sendMessage('voice_trigger');
    }, [sendMessage]);

    const clearHistory = useCallback(() => {
        sendMessage('clear_history');
    }, [sendMessage]);

    const stopSpeaking = useCallback(() => {
        sendMessage('stop_speaking');
    }, [sendMessage]);

    const recalibrateMic = useCallback(() => {
        sendMessage('recalibrate_mic');
    }, [sendMessage]);

    return {
        connected,
        agentState,
        conversation,
        transcript,
        streamingText,
        isStreaming,
        status,
        dashboard,
        toolActivity,
        sendText,
        triggerVoice,
        clearHistory,
        stopSpeaking,
        refreshDashboard: fetchDashboard,
        detections,
        sendMessage,
        audioLevel,
        queueSize,
        recalibrateMic,
        piStatus,
        routeInfo,
        piHealth,
    };
}
