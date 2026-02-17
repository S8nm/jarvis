/**
 * ShinyText — Animated shimmer sweep across text (inspired by react-bits)
 * Pure CSS, zero dependencies. Perfect for panel headers and status text.
 */
import './ShinyText.css';

export default function ShinyText({
  children,
  speed = 3,
  className = '',
  disabled = false,
}) {
  return (
    <span
      className={`shiny-text ${disabled ? '' : 'shiny-active'} ${className}`}
      style={{ '--shiny-speed': `${speed}s` }}
    >
      {children}
    </span>
  );
}
