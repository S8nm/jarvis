import { useRef, useEffect, memo } from 'react';
import ShinyText from './ShinyText';
import { formatMessage } from '../utils/formatMessage';

/**
 * Conversation Log — scrollable chat history between user and Jarvis.
 * Shows streaming text with typing cursor, markdown formatting, and improved empty state.
 */
const ConversationLog = memo(function ConversationLog({ conversation, streamingText, isStreaming }) {
    const scrollRef = useRef(null);

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [conversation, streamingText, isStreaming]);

    return (
        <div className="conversation-container glass-panel">
            <div className="panel-header">
                <span className="indicator" aria-hidden="true" />
                <ShinyText speed={5}>Communication Log</ShinyText>
            </div>

            <div className="conversation-log" ref={scrollRef} role="log" aria-live="polite" aria-label="Conversation history">
                {conversation.length === 0 && !isStreaming && (
                    <div className="empty-state">
                        <div className="empty-state-icon" aria-hidden="true">◈</div>
                        <div className="empty-state-text">Awaiting communication, sir.</div>
                        <div className="empty-state-hint">
                            Press <kbd>/</kbd> to type or <kbd>Space</kbd> to talk
                        </div>
                    </div>
                )}

                {conversation.map((msg, i) => (
                    <div key={`msg-${msg.timestamp || 'no-ts'}-${i}`} className={`message ${msg.role}`} role="article">
                        <div className="message-bubble">
                            {msg.role === 'assistant' ? formatMessage(msg.content) : msg.content}
                        </div>
                        <div className="message-meta">
                            <span className="message-label">
                                {msg.role === 'user' ? 'You' : 'J.A.R.V.I.S.'}
                            </span>
                            {msg.route && (
                                <span className={`route-badge route-${msg.route}`}>
                                    {msg.route === 'tool_direct' ? 'direct' : msg.route}
                                </span>
                            )}
                            {msg.timestamp && (
                                <span>{formatTime(msg.timestamp)}</span>
                            )}
                        </div>
                    </div>
                ))}

                {isStreaming && streamingText && (
                    <div className="message assistant" role="article">
                        <div className="message-bubble">
                            {formatMessage(streamingText)}
                            <span className="typing-cursor" aria-label="JARVIS is typing" />
                        </div>
                        <div className="message-meta">
                            <span className="message-label">J.A.R.V.I.S.</span>
                            <span>typing...</span>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
});

export default ConversationLog;

function formatTime(isoString) {
    try {
        const d = new Date(isoString);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
        return '';
    }
}
