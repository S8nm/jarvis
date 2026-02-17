// TranscriptPanel — shows what JARVIS heard with confidence
import ShinyText from './ShinyText';

/**
 * Transcript Panel — "What Jarvis Heard"
 * Shows the raw transcript text with confidence score indicator.
 */
export default function TranscriptPanel({ transcript }) {
    const { text, confidence } = transcript || {};
    const pct = Math.round((confidence || 0) * 100);

    return (
        <div className="transcript-panel glass-panel">
            <div className="panel-header">
                <span className="indicator" />
                <ShinyText speed={5}>What I Heard</ShinyText>
            </div>

            <div className="transcript-content">
                {text ? (
                    <div className="transcript-text">"{text}"</div>
                ) : (
                    <div className="transcript-text transcript-empty">
                        Listening for your command, sir...
                    </div>
                )}

                <div className="confidence-wrapper" style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '16px' }}>
                    <div className="confidence-track" style={{ flex: 1, height: '6px', background: '#0a1428', display: 'flex', gap: '2px' }}>
                        {[...Array(20)].map((_, i) => (
                            <div
                                key={i}
                                style={{
                                    flex: 1,
                                    background: i < (pct / 5) ? 'var(--accent-primary)' : 'transparent',
                                    opacity: i < (pct / 5) ? 1 : 0.2,
                                    boxShadow: i < (pct / 5) ? '0 0 5px var(--accent-primary)' : 'none'
                                }}
                            />
                        ))}
                    </div>
                    <span className="confidence-label" style={{ fontFamily: 'Share Tech Mono', color: 'var(--accent-primary)' }}>
                        {pct}%
                    </span>
                </div>
            </div>
        </div>
    );
}
