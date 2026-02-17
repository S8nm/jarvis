import { Component } from 'react';

class ErrorBoundary extends Component {
    state = { hasError: false, error: null };

    static getDerivedStateFromError(error) {
        return { hasError: true, error };
    }

    componentDidCatch(error, errorInfo) {
        console.error('[JARVIS] Component crash:', error, errorInfo);
    }

    render() {
        if (this.state.hasError) {
            return (
                <div className="glass-panel" style={{ padding: 'var(--gap-lg)', textAlign: 'center' }}>
                    <div className="panel-header">
                        <span className="indicator" style={{ background: 'var(--accent-alert)' }} />
                        <span style={{ color: 'var(--accent-alert)' }}>SYSTEM ERROR</span>
                    </div>
                    <p style={{ color: 'var(--text-dim)', margin: 'var(--gap-md) 0', fontFamily: 'var(--font-mono)', fontSize: '0.75rem' }}>
                        {this.state.error?.message || 'Module offline'}
                    </p>
                    <button
                        className="hud-control-btn"
                        onClick={() => this.setState({ hasError: false, error: null })}
                    >
                        ↺ RETRY
                    </button>
                </div>
            );
        }
        return this.props.children;
    }
}

export default ErrorBoundary;
