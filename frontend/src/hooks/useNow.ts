import { useEffect, useState } from 'react';

/** Current timestamp (ms) that re-renders every `intervalMs` — drives live-ticking elapsed-time displays. */
export function useNow(intervalMs = 1000, enabled = true): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, enabled]);

  return now;
}
