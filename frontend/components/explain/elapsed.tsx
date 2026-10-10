"use client";

import { useEffect, useState } from "react";
import { formatClock } from "@/lib/format";

/**
 * Elapsed generation time. Anchored to the server's `elapsed_seconds` from the latest
 * poll and ticked locally in between, so it never drifts from the backend's clock.
 */
export function ElapsedClock({ baseSeconds, receivedAt }: { baseSeconds: number; receivedAt: number }) {
  const [now, setNow] = useState(receivedAt);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, []);

  const seconds = baseSeconds + Math.max(0, now - receivedAt) / 1000;
  return (
    <time className="tabular-nums" dateTime={`PT${Math.floor(seconds)}S`}>
      {formatClock(seconds)}
    </time>
  );
}
