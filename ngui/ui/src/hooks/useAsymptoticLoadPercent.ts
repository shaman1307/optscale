import { useEffect, useState } from "react";

/**
 * Progress percent while a request is in flight.
 * Approaches 99% asymptotically based on expectedSec; jumps to 100 when done.
 * Same idea as Resources page weights — unfinished work never claims 100% from the clock alone.
 */
export const useAsymptoticLoadPercent = (isLoading: boolean, expectedSec: number, resetKey?: unknown) => {
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    if (!isLoading) {
      setElapsedMs(0);
      return undefined;
    }

    const startedAt = Date.now();
    setElapsedMs(0);
    const intervalId = window.setInterval(() => {
      setElapsedMs(Date.now() - startedAt);
    }, 250);

    return () => window.clearInterval(intervalId);
  }, [isLoading, resetKey, expectedSec]);

  if (!isLoading) {
    return 100;
  }

  const fraction = 1 - Math.exp(-elapsedMs / (expectedSec * 1000));
  return Math.min(99, Math.round(fraction * 100));
};
