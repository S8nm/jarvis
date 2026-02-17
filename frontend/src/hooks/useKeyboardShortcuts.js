import { useEffect } from 'react';
import { useConnection, useVoice, useActions } from '../contexts/JarvisContext';

/**
 * Global keyboard shortcuts — extracted from App.jsx.
 * Space = connect voice, Esc = disconnect/stop, Ctrl+L = clear, / = focus input
 */
export function useKeyboardShortcuts() {
    const { agentState } = useConnection();
    const { voiceStatus } = useVoice();
    const { connectVoice, disconnectVoice, stopSpeaking, clearHistory, clearVoiceText } = useActions();

    useEffect(() => {
        const handleGlobalKey = (e) => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

            if (e.code === 'Space' && voiceStatus === 'disconnected') {
                e.preventDefault();
                connectVoice();
            }
            if (e.key === 'Escape') {
                if (voiceStatus === 'connected' || voiceStatus === 'connecting') {
                    disconnectVoice();
                } else if (agentState === 'SPEAKING') {
                    stopSpeaking();
                }
            }
            if (e.ctrlKey && e.key === 'l') {
                e.preventDefault();
                clearHistory();
                clearVoiceText();
            }
            if (e.key === '/' && agentState === 'IDLE') {
                e.preventDefault();
                document.querySelector('#main-input')?.focus();
            }
        };
        window.addEventListener('keydown', handleGlobalKey);
        return () => window.removeEventListener('keydown', handleGlobalKey);
    }, [agentState, voiceStatus, connectVoice, disconnectVoice, stopSpeaking, clearHistory, clearVoiceText]);
}
