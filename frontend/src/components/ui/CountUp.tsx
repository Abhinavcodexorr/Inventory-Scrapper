import { useEffect, useRef, useState } from 'react';
import { useAppSettings } from '../../theme/settings';

interface Props {
  value: number;
  /** Animation length in ms. */
  duration?: number;
  decimals?: number;
  suffix?: string;
  prefix?: string;
  /** Group thousands with locale separators. */
  format?: boolean;
}

const easeOutExpo = (t: number) => (t === 1 ? 1 : 1 - Math.pow(2, -10 * t));

/**
 * Animated number. Counts from the previously displayed value so a background
 * refetch ticks up from where it was rather than restarting at zero.
 * Falls back to the plain value when motion is reduced.
 */
export function CountUp({ value, duration = 900, decimals = 0, suffix, prefix, format = true }: Props) {
  const { settings } = useAppSettings();
  const prefersReduced =
    settings.reduceMotion ||
    (typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  const [display, setDisplay] = useState(prefersReduced ? value : 0);
  const fromRef = useRef(0);
  const frameRef = useRef(0);

  useEffect(() => {
    if (prefersReduced) {
      setDisplay(value);
      fromRef.current = value;
      return;
    }

    const from = fromRef.current;
    const delta = value - from;
    if (delta === 0) return;

    const start = performance.now();
    const tick = (now: number) => {
      const progress = Math.min((now - start) / duration, 1);
      const next = from + delta * easeOutExpo(progress);
      setDisplay(next);
      if (progress < 1) {
        frameRef.current = requestAnimationFrame(tick);
      } else {
        fromRef.current = value;
      }
    };

    frameRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frameRef.current);
  }, [value, duration, prefersReduced]);

  const rounded = decimals > 0 ? Number(display.toFixed(decimals)) : Math.round(display);
  const body = format
    ? rounded.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })
    : rounded.toFixed(decimals);

  return (
    <span aria-label={`${prefix ?? ''}${value}${suffix ?? ''}`}>
      {prefix}
      {body}
      {suffix}
    </span>
  );
}
