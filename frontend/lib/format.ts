/** 7.5 -> "0:07", 75 -> "1:15" (video timestamps). */
export function formatTimestamp(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** 74 -> "1:14" — a running clock for elapsed time. */
export function formatClock(seconds: number): string {
  return formatTimestamp(seconds);
}

/** 134 -> "2 min 14 s", 42 -> "42 s" — a finished duration in words. */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return rest ? `${m} min ${rest} s` : `${m} min`;
}

/** Seconds between two ISO timestamps, or null if either is missing/invalid. */
export function secondsBetween(startIso: string | null | undefined, endIso: string | null | undefined): number | null {
  if (!startIso || !endIso) return null;
  const a = Date.parse(startIso);
  const b = Date.parse(endIso);
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return null;
  return (b - a) / 1000;
}

/** "Animating scene 2 of 4" -> 1 (0-based scene index), otherwise null. */
export function sceneIndexFromDetail(detail: string | null | undefined): number | null {
  if (!detail) return null;
  const match = /scene\s+(\d+)\s+of\s+\d+/i.exec(detail);
  return match ? Number(match[1]) - 1 : null;
}
