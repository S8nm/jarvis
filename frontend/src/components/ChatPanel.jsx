import { useState, useCallback, useRef, memo } from 'react';
import { useConnection, useConversation, useSystem, useVoice, useActions } from '../contexts/JarvisContext';
import ConversationLog from './ConversationLog';
import AudioLevelBar from './AudioLevelBar';
import QuickActions from './QuickActions';

const ChatPanel = memo(function ChatPanel() {
    const { agentState } = useConnection();
    const { conversation, streamingText, isStreaming } = useConversation();
    const { audioLevel } = useSystem();
    const { voiceStatus } = useVoice();
    const { sendText, stopSpeaking, connectVoice, disconnectVoice } = useActions();

    const [inputText, setInputText] = useState('');
    const textareaRef = useRef(null);
    const isBusy = agentState === 'THINKING' || agentState === 'EXECUTING';

    const toggleVoice = useCallback(() => {
        if (voiceStatus === 'connected' || voiceStatus === 'connecting') {
            disconnectVoice();
        } else {
            connectVoice();
        }
    }, [voiceStatus, connectVoice, disconnectVoice]);

    const handleSubmit = useCallback((e) => {
        e.preventDefault();
        const text = inputText.trim();
        if (!text) return;
        sendText(text);
        setInputText('');
        // Return focus to textarea after send
        textareaRef.current?.focus();
    }, [inputText, sendText]);

    const handleKeyDown = useCallback((e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSubmit(e);
        }
    }, [handleSubmit]);

    // Auto-resize textarea
    const handleInput = useCallback((e) => {
        setInputText(e.target.value);
        const el = e.target;
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 120) + 'px'; // max ~4 lines
    }, []);

    return (
        <div className="hud-left">
            <ConversationLog
                conversation={conversation}
                streamingText={streamingText}
                isStreaming={isStreaming}
            />

            <AudioLevelBar
                audioLevel={audioLevel}
                isActive={agentState === 'LISTENING'}
            />

            <div className="input-area glass-panel">
                <form className="input-wrapper" onSubmit={handleSubmit}>
                    <button
                        type="button"
                        className={`mic-btn ${voiceStatus === 'connected' ? 'active voice-live' : voiceStatus === 'connecting' ? 'connecting' : agentState === 'LISTENING' ? 'active' : ''}`}
                        onClick={toggleVoice}
                        title={voiceStatus === 'connected' ? 'Voice active — click to disconnect' : voiceStatus === 'connecting' ? 'Connecting...' : 'Click to start voice'}
                        aria-label={voiceStatus === 'connected' ? 'Disconnect voice' : 'Start voice'}
                    >
                        {voiceStatus === 'connected' ? '🔊' : voiceStatus === 'connecting' ? '⏳' : '🎤'}
                    </button>
                    <textarea
                        id="main-input"
                        ref={textareaRef}
                        className="text-input"
                        value={inputText}
                        onChange={handleInput}
                        onKeyDown={handleKeyDown}
                        placeholder="Type a command, sir..."
                        disabled={agentState === 'LISTENING' || isBusy}
                        rows={1}
                        style={{ resize: 'none', overflow: 'hidden' }}
                        aria-label="Message input"
                    />
                    <button
                        type="submit"
                        className="send-btn"
                        disabled={!inputText.trim() || isBusy}
                        title="Send"
                        aria-label="Send message"
                    >
                        ➤
                    </button>
                </form>
            </div>

            <div className="shortcut-hint" aria-hidden="true">
                {voiceStatus === 'connected'
                    ? <><kbd>Esc</kbd> disconnect &nbsp; <kbd>/</kbd> type &nbsp; <kbd>Ctrl+L</kbd> clear</>
                    : <><kbd>Space</kbd> talk &nbsp; <kbd>/</kbd> type &nbsp; <kbd>Esc</kbd> stop &nbsp; <kbd>Ctrl+L</kbd> clear</>
                }
            </div>

            <QuickActions sendText={sendText} populateInput={(text) => { setInputText(text); textareaRef.current?.focus(); }} disabled={isBusy} />
        </div>
    );
});

export default ChatPanel;
