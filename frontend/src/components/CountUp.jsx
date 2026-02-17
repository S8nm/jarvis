/**
 * CountUp — Animated number counter using spring physics (inspired by react-bits)
 * Uses motion library for smooth spring-based number transitions.
 */
import { useEffect, useRef, useState } from 'react';
import { useSpring, useTransform, motion } from 'motion/react';

export default function CountUp({
  to = 0,
  from = 0,
  duration = 1.5,
  decimals = 0,
  suffix = '',
  prefix = '',
  className = '',
  separator = '',
}) {
  const spring = useSpring(from, {
    stiffness: 50,
    damping: 20,
    mass: 1,
    duration: duration * 1000,
  });

  const display = useTransform(spring, (v) => {
    let num = v.toFixed(decimals);
    if (separator) {
      const parts = num.split('.');
      parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, separator);
      num = parts.join('.');
    }
    return `${prefix}${num}${suffix}`;
  });

  useEffect(() => {
    spring.set(to);
  }, [to, spring]);

  return <motion.span className={className}>{display}</motion.span>;
}
