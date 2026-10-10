"use client";

import { AnimatePresence, motion } from "motion/react";
import { Check, X } from "lucide-react";
import { cn } from "@/lib/cn";

/** Plain descriptions of each stage, shown only when the API sends no `detail`. */
const STAGE_HINTS = [
  "Reading your question",
  "Planning the scenes and captions",
  "Writing the animation code",
  "Rendering every frame",
  "",
];

export type StageState = "done" | "active" | "upcoming" | "failed";

function StageIndicator({ state, index }: { state: StageState; index: number }) {
  if (state === "done") {
    return (
      <span className="relative z-10 grid size-8 place-items-center rounded-full bg-teal/15 text-teal ring-1 ring-teal/40">
        <Check className="size-4" strokeWidth={2.5} aria-hidden />
      </span>
    );
  }
  if (state === "failed") {
    return (
      <span className="relative z-10 grid size-8 place-items-center rounded-full bg-rose/15 text-rose ring-1 ring-rose/45">
        <X className="size-4" strokeWidth={2.5} aria-hidden />
      </span>
    );
  }
  if (state === "active") {
    return (
      <span className="relative z-10 grid size-8 place-items-center">
        <span className="absolute inset-0 animate-spin-slow rounded-full bg-[conic-gradient(from_0deg,transparent_0%,#58c4dd_35%,#b394f6_70%,transparent_100%)] [mask:radial-gradient(farthest-side,transparent_calc(100%-2px),#000_calc(100%-2px))]" />
        <span className="absolute inset-0 rounded-full ring-1 ring-blue/20" />
        <span className="size-2.5 animate-pulse-soft rounded-full bg-blue shadow-[0_0_12px_2px_rgb(88_196_221/0.6)]" />
      </span>
    );
  }
  return (
    <span className="relative z-10 grid size-8 place-items-center rounded-full bg-ink-900 font-mono text-xs text-fg-faint ring-1 ring-line-strong">
      {index + 1}
    </span>
  );
}

export function StageList({
  stages,
  stageIndex,
  detail,
  failed = false,
  repairing = false,
  className,
}: {
  stages: string[];
  stageIndex: number;
  detail: string | null;
  failed?: boolean;
  /** The backend is repairing a failed render — tint the detail so it reads as a recoverable hiccup. */
  repairing?: boolean;
  className?: string;
}) {
  return (
    <ol className={cn("relative", className)} aria-label="Generation progress">
      {stages.map((label, i) => {
        const state: StageState =
          i < stageIndex ? "done" : i === stageIndex ? (failed ? "failed" : "active") : "upcoming";
        const isLast = i === stages.length - 1;
        const sub = state === "active" ? detail || STAGE_HINTS[i] || null : null;
        return (
          <li
            key={label}
            className={cn("relative flex gap-3.5", !isLast && "pb-5")}
            aria-current={state === "active" ? "step" : undefined}
          >
            {!isLast && (
              <span
                aria-hidden
                className={cn(
                  "absolute bottom-0 left-4 top-8 w-px -translate-x-1/2",
                  state === "done" ? "bg-gradient-to-b from-teal/60 to-teal/25" : "bg-line-strong",
                )}
              />
            )}
            <StageIndicator state={state} index={i} />
            <div className="min-w-0 flex-1 pt-[5px]">
              <p
                className={cn(
                  "text-[15px] leading-snug transition-colors duration-300",
                  state === "active" && "font-medium text-fg",
                  state === "done" && "text-fg-muted",
                  state === "upcoming" && "text-fg-subtle",
                  state === "failed" && "font-medium text-rose",
                )}
              >
                {label}
                {state === "done" && <span className="sr-only"> (done)</span>}
                {state === "failed" && <span className="sr-only"> (stopped here)</span>}
              </p>
              <AnimatePresence mode="wait" initial={false}>
                {sub && (
                  <motion.p
                    key={sub}
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    transition={{ duration: 0.25 }}
                    className={cn("mt-1 text-[13px] leading-snug", repairing ? "text-amber" : "text-fg-muted")}
                  >
                    {sub}
                  </motion.p>
                )}
              </AnimatePresence>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
