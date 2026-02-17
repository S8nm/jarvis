import { useState, useEffect, memo } from 'react';
import { useConnection, useSystem, useVoice } from '../contexts/JarvisContext';

/**
 * Status Bar — bottom bar with system status indicators and clock.
 * Now consumes context directly — no props needed.
 */
const StatusBar = memo(function StatusBar() {
    const { connected, agentState } = useConnection();
    const { status } = useSystem();
    const { voiceStatus, isVoiceActive } = useVoice();

    const [time, setTime] = useState(new Date());

    useEffect(() => {
        const interval = setInterval(() => setTime(new Date()), 1000);
        return () => clearInterval(interval);
    }, []);

    const indicators = [
        {
            label: 'Connection',
            active: connected,
            className: connected ? 'active' : 'warning',
        },
        {
            label: 'Wake Word',
            active: status?.wake_word_active,
            className: status?.wake_word_active ? 'active' : '',
        },
        {
            label: 'Microphone',
            active: agentState === 'LISTENING',
            className: agentState === 'LISTENING' ? 'recording' : (connected ? 'active' : ''),
        },
        {
            label: 'Camera',
            active: false,
            className: '',
        },
        {
            label: 'LLM',
            active: status?.ollama_connected,
            className: status?.ollama_connected ? 'active' : 'warning',
        },
        {
            label: 'TTS',
            active: status?.tts_ready || voiceStatus === 'connected',
            className: voiceStatus === 'connected' ? 'active' : (status?.tts_ready ? 'active' : ''),
        },
        {
            label: 'Voice',
            active: voiceStatus === 'connected',
            className: voiceStatus === 'connected' ? (isVoiceActive ? 'recording' : 'active') : (voiceStatus === 'connecting' ? 'warning' : ''),
        },
    ];

    return (
        <div className="status-bar" role="status" aria-label="System status">
            <div className="status-indicators">
                {indicators.map((ind, i) => (
                    <div key={i} className="status-item" aria-label={`${ind.label}: ${ind.active ? 'online' : 'offline'}`}>
                        <div className={`status-dot ${ind.className}`} aria-hidden="true" />
                        <span>{ind.label}</span>
                    </div>
                ))}
            </div>

            <div className="status-brand">
                J.A.R.V.I.S. PROTOCOL v0.1
            </div>

            <div className="status-time">
                {time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                {' · '}
                {time.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })}
            </div>
        </div>
    );
});

export default StatusBar;
