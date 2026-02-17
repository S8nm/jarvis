import { memo } from 'react';

const BarMeter = memo(function BarMeter({ label, value, color, suffix }) {
    const pct = Math.min(100, Math.max(0, value));
    return (
        <div className="bar-meter">
            <div className="bar-meter-header">
                <span className="bar-meter-label">{label}</span>
                <span className="bar-meter-value">{suffix || `${Math.round(pct)}%`}</span>
            </div>
            <div className="bar-meter-track" role="meter" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
                <div className="bar-meter-fill" style={{ width: `${pct}%`, background: color }} />
            </div>
        </div>
    );
});

export default BarMeter;
