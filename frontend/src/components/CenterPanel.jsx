import { useState, useCallback, memo } from 'react';
import { useConnection, useVoice, useActions } from '../contexts/JarvisContext';
import GreetingClock from './GreetingClock';
import Orb from './Orb';
import ErrorBoundary from './ErrorBoundary';

const CenterPanel = memo(function CenterPanel({ onToggleCamera, showCamera }) {
    const { agentState } = useConnection();
    const { voiceStatus, personaplexText } = useVoice();
    const { stopSpeaking, clearHistory, connectVoice, disconnectVoice, clearVoiceText } = useActions();

    const toggleVoice = useCallback(() => {
        if (voiceStatus === 'connected' || voiceStatus === 'connecting') {
            disconnectVoice();
        } else {
            connectVoice();
        }
    }, [voiceStatus, connectVoice, disconnectVoice]);

    return (
        <div className="hud-center">
            <GreetingClock />

            <div className="orb-section">
                <div className="orb-wrapper">
                    <ErrorBoundary>
                        <Orb state={voiceStatus === 'connected' ? 'LISTENING' : agentState} />
                    </ErrorBoundary>
                </div>

                <div className="hud-center-controls" role="toolbar" aria-label="JARVIS controls">
                    {agentState === 'SPEAKING' && (
                        <button className="hud-control-btn" onClick={stopSpeaking} aria-label="Stop speaking">
                            ⏹ STOP
                        </button>
                    )}
                    <button
                        className={`hud-control-btn ${voiceStatus === 'connected' ? 'active' : ''}`}
                        onClick={toggleVoice}
                        style={voiceStatus === 'connected' ? { borderColor: 'var(--neon-green)', color: 'var(--neon-green)' } : {}}
                        aria-label={voiceStatus === 'connected' ? 'Disconnect voice' : 'Connect voice'}
                        aria-pressed={voiceStatus === 'connected'}
                    >
                        {voiceStatus === 'connected' ? '🔊 VOICE ON' : voiceStatus === 'connecting' ? '⏳ CONNECTING' : '🎙 VOICE'}
                    </button>
                    <button className="hud-control-btn" onClick={clearHistory} aria-label="Clear conversation">
                        ↺ CLEAR
                    </button>
                    <button
                        className={`hud-control-btn ${showCamera ? 'active' : ''}`}
                        onClick={onToggleCamera}
                        style={showCamera ? { borderColor: 'var(--neon-green)', color: 'var(--neon-green)' } : {}}
                        aria-label={showCamera ? 'Disable camera' : 'Enable camera'}
                        aria-pressed={showCamera}
                    >
                        👁 VISION
                    </button>
                </div>
            </div>

            {/* PersonaPlex live transcript */}
            {voiceStatus === 'connected' && personaplexText && (
                <div className="personaplex-transcript glass-panel" role="log" aria-label="Voice transcript" aria-live="polite">
                    <div className="pp-transcript-header">
                        <span className="pp-live-dot" aria-hidden="true" />
                        <span>JARVIS Voice</span>
                        <button className="pp-clear-btn" onClick={clearVoiceText} title="Clear transcript" aria-label="Clear voice transcript">
                            ×
                        </button>
                    </div>
                    <div className="pp-transcript-text">{personaplexText}</div>
                </div>
            )}
        </div>
    );
});

export default CenterPanel;
