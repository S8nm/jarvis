import { createContext, useContext, useMemo, useCallback } from 'react';
import { useJarvisSocket } from '../hooks/useJarvisSocket';
import { usePersonaPlex } from '../hooks/usePersonaPlex';

// Split contexts to isolate re-renders — only consumers of a specific
// context slice re-render when that slice changes.
const ConnectionContext = createContext(null);
const ConversationContext = createContext(null);
const SystemContext = createContext(null);
const VoiceContext = createContext(null);
const ActionsContext = createContext(null);

export function JarvisProvider({ children }) {
    const jarvis = useJarvisSocket();
    const voice = usePersonaPlex();

    const connection = useMemo(() => ({
        connected: jarvis.connected,
        agentState: jarvis.agentState,
    }), [jarvis.connected, jarvis.agentState]);

    const conversation = useMemo(() => ({
        conversation: jarvis.conversation,
        transcript: jarvis.transcript,
        streamingText: jarvis.streamingText,
        isStreaming: jarvis.isStreaming,
    }), [jarvis.conversation, jarvis.transcript, jarvis.streamingText, jarvis.isStreaming]);

    const system = useMemo(() => ({
        status: jarvis.status,
        dashboard: jarvis.dashboard,
        toolActivity: jarvis.toolActivity,
        audioLevel: jarvis.audioLevel,
        detections: jarvis.detections,
        queueSize: jarvis.queueSize,
    }), [jarvis.status, jarvis.dashboard, jarvis.toolActivity, jarvis.audioLevel, jarvis.detections, jarvis.queueSize]);

    const voiceCtx = useMemo(() => ({
        voiceStatus: voice.voiceStatus,
        personaplexText: voice.personaplexText,
        isVoiceActive: voice.isVoiceActive,
    }), [voice.voiceStatus, voice.personaplexText, voice.isVoiceActive]);

    // Actions are stable useCallback refs — this context value almost never changes
    const actions = useMemo(() => ({
        sendText: jarvis.sendText,
        sendMessage: jarvis.sendMessage,
        triggerVoice: jarvis.triggerVoice,
        clearHistory: jarvis.clearHistory,
        stopSpeaking: jarvis.stopSpeaking,
        recalibrateMic: jarvis.recalibrateMic,
        refreshDashboard: jarvis.refreshDashboard,
        connectVoice: voice.connect,
        disconnectVoice: voice.disconnect,
        clearVoiceText: voice.clearText,
    }), [
        jarvis.sendText, jarvis.sendMessage, jarvis.triggerVoice,
        jarvis.clearHistory, jarvis.stopSpeaking, jarvis.recalibrateMic,
        jarvis.refreshDashboard, voice.connect, voice.disconnect, voice.clearText,
    ]);

    return (
        <ActionsContext.Provider value={actions}>
            <ConnectionContext.Provider value={connection}>
                <ConversationContext.Provider value={conversation}>
                    <SystemContext.Provider value={system}>
                        <VoiceContext.Provider value={voiceCtx}>
                            {children}
                        </VoiceContext.Provider>
                    </SystemContext.Provider>
                </ConversationContext.Provider>
            </ConnectionContext.Provider>
        </ActionsContext.Provider>
    );
}

export const useConnection = () => useContext(ConnectionContext);
export const useConversation = () => useContext(ConversationContext);
export const useSystem = () => useContext(SystemContext);
export const useVoice = () => useContext(VoiceContext);
export const useActions = () => useContext(ActionsContext);
