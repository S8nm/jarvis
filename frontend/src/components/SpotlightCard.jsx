/**
 * SpotlightCard — Mouse-following radial gradient spotlight (inspired by react-bits)
 * Pure CSS + minimal JS for mouse tracking. No dependencies.
 */
import { useCallback } from 'react';
import './SpotlightCard.css';

export default function SpotlightCard({ children, className = '', as: Tag = 'div', ...props }) {
  const handleMouseMove = useCallback((e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    e.currentTarget.style.setProperty('--x', `${x}px`);
    e.currentTarget.style.setProperty('--y', `${y}px`);
  }, []);

  return (
    <Tag
      className={`spotlight-card ${className}`}
      onMouseMove={handleMouseMove}
      {...props}
    >
      {children}
    </Tag>
  );
}
