import { useEffect, useRef, useState } from "react";

/**
 * Coalesce high-frequency values to at most one update per ``intervalMs``.
 * Terminal callers can request an immediate flush. Ordinary effect cleanup
 * never commits state: doing so on each incoming value defeats throttling.
 */
export function useThrottledValue<T>(
  value: T,
  intervalMs = 50,
  flush = false
): T {
  const [displayed, setDisplayed] = useState(value);
  const latest = useRef(value);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const last = useRef(Date.now());

  useEffect(() => {
    latest.current = value;
    if (flush) {
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
      last.current = Date.now();
      setDisplayed(value);
      return;
    }
    const now = Date.now();
    const elapsed = now - last.current;
    if (elapsed >= intervalMs && timer.current === null) {
      last.current = now;
      setDisplayed(value);
      return;
    }
    if (timer.current !== null) return;
    timer.current = setTimeout(() => {
      last.current = Date.now();
      timer.current = null;
      setDisplayed(latest.current);
    }, Math.max(1, intervalMs - elapsed));
  }, [value, intervalMs, flush]);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    []
  );

  return displayed;
}
