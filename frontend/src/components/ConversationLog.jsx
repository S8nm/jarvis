import { useRef, useEffect } from 'react';
import ShinyText from './ShinyText';

/**
 * Conversation Log — scrollable chat history between user and Jarvis.
 * Shows streaming text with a typing cursor effect.
 */
export default function ConversationLog({ conversation, streamingText, isStreaming }) {
    const scrollRef = useRef(null);

    // Auto-scroll to bottom on new messages or when streaming ends
    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [conversation, streamingText, isStreaming]);

    return (
        <div className="conversation-container glass-panel">
            <div className="panel-header">
                <span className="indicator" />
                <ShinyText speed={5}>Communication Log</ShinyText>
            </div>

            <div className="conversation-log" ref={scrollRef}>
                {conversation.length === 0 && !isStreaming && (
                    <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        height: '100%',
                        color: 'var(--text-dim)',
                        fontStyle: 'italic',
                        fontSize: '0.85rem'
                    }}>
                        Awaiting communication, sir.
                    </div>
                )}

                {conversation.map((msg, i) => (
                    <div key={msg.timestamp ? `${msg.timestamp}-${i}` : i} className={`message ${msg.role}`}>
                        <div className="message-bubble">
                            {msg.content}
                        </div>
                        <div className="message-meta">
                            <span className="message-label">
                                {msg.role === 'user' ? 'You' : 'J.A.R.V.I.S.'}
                            </span>
                            {msg.timestamp && (
                                <span>{formatTime(msg.timestamp)}</span>
                            )}
                        </div>
                    </div>
                ))}

                {/* Streaming response */}
                {isStreaming && streamingText && (
                    <div className="message assistant">
                        <div className="message-bubble">
                            {streamingText}
                            <span className="typing-cursor" />
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
}

function formatTime(isoString) {
    try {
        const d = new Date(isoString);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
        return '';
    }
}
