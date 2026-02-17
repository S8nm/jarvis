/**
 * DecryptedText — Scramble/decrypt text reveal animation (inspired by react-bits)
 * Lightweight vanilla React, no external dependencies.
 */
import { useState, useEffect, useRef, useCallback } from 'react';

const CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()_+-=[]{}|;:,.<>?/~`';

export default function DecryptedText({
  text = '',
  speed = 50,
  revealDelay = 0,
  sequential = true,
  className = '',
  onComplete,
  animateOn = 'mount', // 'mount' | 'hover' | 'view'
}) {
  const [displayed, setDisplayed] = useState(text);
  const [isDecrypted, setIsDecrypted] = useState(false);
  const ref = useRef(null);
  const intervalRef = useRef(null);

  const scramble = useCallback(() => {
    setIsDecrypted(false);
    let iteration = 0;
    const target = text;

    if (intervalRef.current) clearInterval(intervalRef.current);

    intervalRef.current = setInterval(() => {
      setDisplayed(
        target
          .split('')
          .map((char, i) => {
            if (char === ' ') return ' ';
            if (sequential && i < iteration) return target[i];
            if (!sequential && Math.random() > 0.5 && iteration > 3) return target[i];
            return CHARS[Math.floor(Math.random() * CHARS.length)];
          })
          .join('')
      );

      iteration += 1;

      if (iteration > target.length) {
        clearInterval(intervalRef.current);
        setDisplayed(target);
        setIsDecrypted(true);
        onComplete?.();
      }
    }, speed);
  }, [text, speed, sequential, onComplete]);

  // Mount animation
  useEffect(() => {
    if (animateOn === 'mount') {
      const timer = setTimeout(scramble, revealDelay);
      return () => {
        clearTimeout(timer);
        if (intervalRef.current) clearInterval(intervalRef.current);
      };
    }
  }, [animateOn, scramble, revealDelay]);

  // View animation (IntersectionObserver)
  useEffect(() => {
    if (animateOn !== 'view' || !ref.current) return;
    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !isDecrypted) scramble();
      },
      { threshold: 0.5 }
    );
    obs.observe(ref.current);
    return () => obs.disconnect();
  }, [animateOn, scramble, isDecrypted]);

  const handleMouseEnter = animateOn === 'hover' ? scramble : undefined;

  return (
    <span
      ref={ref}
      className={className}
      onMouseEnter={handleMouseEnter}
      style={{
        fontFamily: 'inherit',
        letterSpacing: 'inherit',
        display: 'inline-block',
      }}
    >
      {displayed}
    </span>
  );
}
