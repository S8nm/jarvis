import { memo } from 'react';

const ServiceDot = memo(function ServiceDot({ label, active, connecting }) {
    const cls = connecting ? 'warning' : active ? 'active' : '';
    return (
        <div className="service-item">
            <div className={`status-dot ${cls}`} aria-hidden="true" />
            <span>{label}</span>
        </div>
    );
});

export default ServiceDot;
