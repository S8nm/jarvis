import { useState, useCallback, useMemo, lazy, Suspense } from 'react';
import { JarvisProvider, useConnection, useSystem } from './contexts/JarvisContext';
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts';
import ErrorBoundary from './components/ErrorBoundary';
import SkipToContent from './components/SkipToContent';
import TopBar from './components/TopBar';
import ChatPanel from './components/ChatPanel';
import CenterPanel from './components/CenterPanel';
import WidgetsPanel from './components/WidgetsPanel';
import StatusBar from './components/StatusBar';
import ActivityTabs from './components/ActivityTabs';

const Aurora = lazy(() => import('./components/Aurora'));
const CameraOverlay = lazy(() => import('./components/CameraOverlay'));

// Module-level constant — never changes, never re-creates
const AURORA_COLORS = {
    IDLE:      ['#003366', '#001a33', '#004488'],
    LISTENING: ['#003d1a', '#001a0d', '#00662e'],
    THINKING:  ['#332b00', '#1a1500', '#665500'],
    EXECUTING: ['#331a00', '#1a0d00', '#663300'],
    SPEAKING:  ['#1a0033', '#0d001a', '#330066'],
    ERROR:     ['#330011', '#1a0008', '#660022'],
};

function AppLayout() {
    useKeyboardShortcuts();

    const { connected, agentState } = useConnection();
    const { toolActivity, status, detections } = useSystem();
    const [showCamera, setShowCamera] = useState(false);

    const currentAuroraColors = useMemo(
        () => AURORA_COLORS[agentState] || AURORA_COLORS.IDLE,
        [agentState]
    );

    const toggleCamera = useCallback(() => setShowCamera(prev => !prev), []);

    return (
        <div className="jarvis-app" role="application" aria-label="JARVIS AI Assistant">
            <SkipToContent />

            {/* Aurora WebGL Background */}
            <Suspense fallback={null}>
                <ErrorBoundary>
                    <Aurora
                        colorStops={currentAuroraColors}
                        amplitude={1.2}
                        speed={0.6}
                        blend={0.5}
                    />
                </ErrorBoundary>
            </Suspense>

            {!connected && (
                <div className="connection-alert" role="alert" aria-live="assertive">
                    Backend offline — attempting reconnection...
                </div>
            )}

            <div className="hud-layout">
                <TopBar />

                <div className="hud-main-grid" role="main">
                    <ErrorBoundary><ChatPanel /></ErrorBoundary>
                    <ErrorBoundary><CenterPanel onToggleCamera={toggleCamera} showCamera={showCamera} /></ErrorBoundary>
                    <ErrorBoundary><WidgetsPanel /></ErrorBoundary>
                </div>
            </div>

            <StatusBar />
            <ActivityTabs />

            {showCamera && (
                <Suspense fallback={<div className="camera-pip glass-panel" style={{ padding: 'var(--gap-lg)', textAlign: 'center', color: 'var(--text-dim)' }}>Loading camera...</div>}>
                    <ErrorBoundary>
                        <CameraOverlay
                            isActive={showCamera}
                            detections={detections}
                        />
                    </ErrorBoundary>
                </Suspense>
            )}
        </div>
    );
}

export default function App() {
    return (
        <JarvisProvider>
            <AppLayout />
        </JarvisProvider>
    );
}
