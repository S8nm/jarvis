import { memo } from 'react';
import { useSystem } from '../contexts/JarvisContext';
import BarMeter from './BarMeter';
import ShinyText from './ShinyText';

const GpuMonitor = memo(function GpuMonitor() {
    const { status } = useSystem();
    const gpu = status?.system?.gpu || {};

    if (!gpu.name) return null;

    return (
        <div className="gpu-widget glass-panel">
            <div className="panel-header">
                <span className="indicator" aria-hidden="true" />
                <ShinyText speed={5}>GPU — {gpu.name}</ShinyText>
            </div>
            <div className="gpu-bars">
                <BarMeter label="Utilization" value={gpu.utilization || 0} color="var(--neon-cyan)" />
                <BarMeter
                    label="VRAM"
                    value={gpu.vram_total_gb ? ((gpu.vram_used_gb || 0) / gpu.vram_total_gb * 100) : 0}
                    color="var(--neon-green)"
                    suffix={gpu.vram_used_gb ? `${gpu.vram_used_gb.toFixed(1)}G / ${gpu.vram_total_gb?.toFixed(0)}G` : ''}
                />
                <BarMeter
                    label="Temperature"
                    value={gpu.temperature || 0}
                    color={gpu.temperature > 80 ? 'var(--accent-alert)' : 'var(--neon-yellow)'}
                    suffix={gpu.temperature ? `${gpu.temperature}°C` : ''}
                />
                <BarMeter
                    label="Power"
                    value={gpu.power_draw && gpu.power_limit ? (gpu.power_draw / gpu.power_limit * 100) : 0}
                    color="var(--neon-purple)"
                    suffix={gpu.power_draw ? `${Math.round(gpu.power_draw)}W` : ''}
                />
            </div>
        </div>
    );
});

export default GpuMonitor;
