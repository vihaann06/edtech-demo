"use client";

import type { StoryboardSceneSummary } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatTimestamp } from "@/lib/format";
import { SCENE_ACCENTS } from "./lesson-plan";

/** Index of the scene playing at `time`, or -1 before the first scene starts. */
export function chapterAt(scenes: StoryboardSceneSummary[], time: number): number {
  let current = -1;
  for (let i = 0; i < scenes.length; i++) {
    if (time + 0.05 >= scenes[i].start_time) current = i;
  }
  return current;
}

export function ChapterList({
  scenes,
  currentTime,
  onSeek,
  className,
}: {
  scenes: StoryboardSceneSummary[];
  currentTime: number;
  onSeek: (seconds: number) => void;
  className?: string;
}) {
  const active = chapterAt(scenes, currentTime);
  return (
    <ol className={cn("flex flex-col gap-1", className)}>
      {scenes.map((scene, i) => {
        const isActive = i === active;
        const accent = SCENE_ACCENTS[i % SCENE_ACCENTS.length];
        const progress =
          i < active
            ? 1
            : isActive
              ? Math.min(1, Math.max(0, (currentTime - scene.start_time) / Math.max(scene.duration, 0.1)))
              : 0;
        return (
          <li key={scene.index}>
            <button
              type="button"
              onClick={() => onSeek(scene.start_time)}
              aria-current={isActive ? "true" : undefined}
              aria-label={`Play chapter ${i + 1}: ${scene.concept}, at ${formatTimestamp(scene.start_time)}`}
              className={cn(
                "group/chapter relative flex w-full gap-3 rounded-xl px-3 py-3 text-left transition-colors duration-200",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue/60",
                isActive ? "bg-white/[0.06]" : "hover:bg-white/[0.035]",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "absolute bottom-3 left-0 top-3 w-[3px] rounded-full transition-opacity duration-300",
                  isActive ? "opacity-100" : "opacity-0",
                )}
                style={{ background: accent }}
              />
              <span
                className={cn(
                  "w-9 shrink-0 pt-px font-mono text-xs tabular-nums transition-colors",
                  isActive ? "text-fg" : "text-fg-subtle group-hover/chapter:text-fg-muted",
                )}
              >
                {formatTimestamp(scene.start_time)}
              </span>
              <span className="min-w-0 flex-1">
                <span
                  className={cn(
                    "block text-sm font-medium leading-snug transition-colors",
                    isActive ? "text-fg" : "text-fg-muted group-hover/chapter:text-fg",
                  )}
                >
                  {scene.concept}
                </span>
                <span className="mt-1 block text-[13px] leading-snug text-fg-subtle">{scene.caption}</span>
                <span aria-hidden className="mt-2.5 block h-0.5 overflow-hidden rounded-full bg-white/[0.06]">
                  <span
                    className="block h-full origin-left rounded-full transition-transform duration-300 ease-linear"
                    style={{ background: accent, opacity: isActive ? 1 : 0.4, transform: `scaleX(${progress})` }}
                  />
                </span>
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
