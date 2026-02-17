import { memo } from 'react';
import { useConversation } from '../contexts/JarvisContext';
import { useSystem } from '../contexts/JarvisContext';
import ServicesWidget from './ServicesWidget';
import GpuMonitor from './GpuMonitor';
import TranscriptPanel from './TranscriptPanel';
import SystemPanel from './SystemPanel';

const WidgetsPanel = memo(function WidgetsPanel() {
    const { transcript } = useConversation();
    const { dashboard } = useSystem();

    return (
        <div className="hud-right">
            <ServicesWidget />
            <GpuMonitor />
            <TranscriptPanel transcript={transcript} />
            <SystemPanel dashboard={dashboard} />
        </div>
    );
});

export default WidgetsPanel;
