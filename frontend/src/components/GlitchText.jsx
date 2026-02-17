/**
 * GlitchText — Cyberpunk RGB-split text effect (inspired by react-bits)
 * Pure CSS, zero dependencies. Perfect for the J.A.R.V.I.S. title.
 */
import './GlitchText.css';

export default function GlitchText({
  children,
  speed = 1,
  enableShadows = true,
  enableOnHover = false,
  className = '',
}) {
  const inlineStyles = {
    '--after-duration': `${speed * 3}s`,
    '--before-duration': `${speed * 2}s`,
    '--after-shadow': enableShadows ? '-5px 0 rgba(0, 243, 255, 0.7)' : 'none',
    '--before-shadow': enableShadows ? '5px 0 rgba(136, 0, 255, 0.7)' : 'none',
  };

  return (
    <span
      className={`glitch-text ${enableOnHover ? 'glitch-hover-only' : ''} ${className}`}
      style={inlineStyles}
      data-text={children}
    >
      {children}
    </span>
  );
}
