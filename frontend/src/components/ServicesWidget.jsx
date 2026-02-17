import { memo } from 'react';
import { useConnection, useSystem, useVoice } from '../contexts/JarvisContext';
import ServiceDot from './ServiceDot';
import ShinyText from './ShinyText';

const ServicesWidget = memo(function ServicesWidget() {
    const { connected } = useConnection();
    const { status, piHealth, routeInfo } = useSystem();
    const { voiceStatus } = useVoice();

    const routerEnabled = status?.router?.enabled !== false;
    const piReachable = piHealth?.reachable ?? status?.piStatus?.enabled;

    return (
        <div className="services-widget glass-panel">
            <div className="panel-header">
                <span className="indicator" aria-hidden="true" />
                <ShinyText speed={4}>Services</ShinyText>
            </div>
            <div className="services-grid" role="list" aria-label="Service status">
                <ServiceDot label="Backend" active={connected} />
                <ServiceDot label="Ollama" active={status?.ollama_connected} />
                <ServiceDot label="Claude" active={status?.claude_connected} />
                <ServiceDot label="Router" active={routerEnabled && connected} />
                <ServiceDot label="Pi" active={piReachable} />
                <ServiceDot label="PersonaPlex" active={voiceStatus === 'connected'} connecting={voiceStatus === 'connecting'} />
                <ServiceDot label="TTS" active={status?.tts_ready || voiceStatus === 'connected'} />
                <ServiceDot label="Wake Word" active={status?.wake_word_active} />
            </div>
            {routeInfo && (
                <div className="route-indicator" role="status" aria-label="Last route">
                    <span className={`route-badge route-${routeInfo.target}`}>
                        {routeInfo.target}
                    </span>
                    <span className="route-detail">
                        {routeInfo.intentType} ({routeInfo.classificationMs?.toFixed(1)}ms)
                    </span>
                </div>
            )}
        </div>
    );
});

export default ServicesWidget;
