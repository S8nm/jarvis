import { memo } from 'react';
import { useConnection, useSystem, useVoice } from '../contexts/JarvisContext';
import ServiceDot from './ServiceDot';
import ShinyText from './ShinyText';

const ServicesWidget = memo(function ServicesWidget() {
    const { connected } = useConnection();
    const { status } = useSystem();
    const { voiceStatus } = useVoice();

    return (
        <div className="services-widget glass-panel">
            <div className="panel-header">
                <span className="indicator" aria-hidden="true" />
                <ShinyText speed={4}>Services</ShinyText>
            </div>
            <div className="services-grid" role="list" aria-label="Service status">
                <ServiceDot label="Backend" active={connected} />
                <ServiceDot label="LLM" active={status?.ollama_connected} />
                <ServiceDot label="PersonaPlex" active={voiceStatus === 'connected'} connecting={voiceStatus === 'connecting'} />
                <ServiceDot label="Bridge" active={voiceStatus === 'connected'} connecting={voiceStatus === 'connecting'} />
                <ServiceDot label="TTS" active={status?.tts_ready || voiceStatus === 'connected'} />
                <ServiceDot label="Wake Word" active={status?.wake_word_active} />
            </div>
        </div>
    );
});

export default ServicesWidget;
