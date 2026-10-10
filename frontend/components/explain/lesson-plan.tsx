"use client";

import { AnimatePresence, motion } from "motion/react";
import { Target } from "lucide-react";
import type { StoryboardSummary } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatTimestamp } from "@/lib/format";
import { Spinner } from "@/components/ui/spinner";

export const SCENE_ACCENTS = ["#58c4dd", "#5cd0b3", "#f4d345", "#b394f6", "#f58a8a"];

/**
 * The storyboard written by the AI planner, revealed as soon as the API returns it.
 * Before that, a quiet placeholder explains what will appear here.
 */
export function LessonPlan({
  storyboard,
  activeScene,
  showTitle = true,
  className,
}: {
  storyboard: StoryboardSummary | null;
  /** Hide the title when the page heading already shows it. */
  showTitle?: boolean;
  /** 0-based scene currently being animated (parsed from the API's `detail`), if known. */
  activeScene: number | null;
  className?: string;
}) {
  return (
    <section aria-labelledby="lesson-plan-heading" className={cn("relative", className)}>
      <AnimatePresence mode="wait" initial={false}>
        {storyboard ? (
          <motion.div
            key="plan"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.4 }}
          >
            <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between sm:gap-6">
              <div className="min-w-0">
                <p id="lesson-plan-heading" className="text-xs font-medium uppercase tracking-[0.14em] text-blue">
                  Lesson plan
                </p>
                {showTitle && (
                  <motion.h2
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
                    className="mt-2 text-balance text-2xl font-semibold tracking-[-0.02em] text-fg sm:text-[28px]"
                  >
                    {storyboard.title}
                  </motion.h2>
                )}
              </div>
              <p className="shrink-0 text-xs tabular-nums text-fg-subtle">
                {storyboard.scenes.length} scenes · {formatTimestamp(sceneTotal(storyboard))}
              </p>
            </div>
            {storyboard.learning_objective && (
              <motion.p
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.08, ease: [0.22, 1, 0.36, 1] }}
                className="mt-3 flex max-w-3xl items-start gap-2.5 text-[15px] leading-relaxed text-fg-muted"
              >
                <Target className="mt-1 size-4 shrink-0 text-teal" aria-hidden />
                <span>{storyboard.learning_objective}</span>
              </motion.p>
            )}
            <ol className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {storyboard.scenes.map((scene, i) => {
                const accent = SCENE_ACCENTS[i % SCENE_ACCENTS.length];
                const animating = activeScene === i;
                return (
                  <motion.li
                    key={scene.index}
                    initial={{ opacity: 0, y: 14 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.5, delay: 0.15 + i * 0.09, ease: [0.22, 1, 0.36, 1] }}
                    className={cn(
                      "relative overflow-hidden rounded-2xl border bg-ink-850/70 p-4 transition-colors duration-500 sm:p-5",
                      animating ? "border-blue/40" : "border-line",
                    )}
                  >
                    <span
                      aria-hidden
                      className="absolute inset-x-0 top-0 h-px"
                      style={{ background: `linear-gradient(90deg, transparent, ${accent}, transparent)` }}
                    />
                    <div className="flex items-center justify-between gap-2 font-mono text-[11px] uppercase tracking-wider text-fg-subtle">
                      <span className="flex items-center gap-2">
                        <span aria-hidden className="size-1.5 rounded-full" style={{ background: accent }} />
                        Scene {i + 1}
                      </span>
                      <span className="tabular-nums normal-case">
                        {formatTimestamp(scene.start_time)}–{formatTimestamp(scene.start_time + scene.duration)}
                      </span>
                    </div>
                    <h3 className="mt-3 text-[15px] font-medium leading-snug text-fg">{scene.concept}</h3>
                    <p className="mt-1.5 text-sm leading-relaxed text-fg-muted">{scene.caption}</p>
                    {animating && (
                      <p className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-blue">
                        <Spinner className="size-3" />
                        Animating this scene
                      </p>
                    )}
                  </motion.li>
                );
              })}
            </ol>
          </motion.div>
        ) : (
          <motion.div key="placeholder" exit={{ opacity: 0 }} transition={{ duration: 0.25 }}>
            <p id="lesson-plan-heading" className="text-xs font-medium uppercase tracking-[0.14em] text-fg-subtle">
              Lesson plan
            </p>
            <p className="mt-2 text-[15px] text-fg-muted">
              Your scenes and captions will appear here as soon as the lesson is designed.
            </p>
            <div aria-hidden className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {[0, 1, 2, 3].map((i) => (
                <div
                  key={i}
                  className={cn(
                    "relative overflow-hidden rounded-2xl border border-line bg-white/[0.015] p-5",
                    i > 1 && "hidden sm:block",
                  )}
                >
                  <div className="h-2.5 w-16 rounded-full bg-white/[0.06]" />
                  <div className="mt-5 h-3.5 w-3/5 rounded-full bg-white/[0.07]" />
                  <div className="mt-3 h-2.5 w-full rounded-full bg-white/[0.045]" />
                  <div className="mt-2 h-2.5 w-4/5 rounded-full bg-white/[0.045]" />
                  <div
                    className="absolute inset-0 animate-shimmer bg-[linear-gradient(90deg,transparent,rgb(255_255_255/0.035),transparent)]"
                    style={{ animationDelay: `${i * 0.15}s` }}
                  />
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

function sceneTotal(storyboard: StoryboardSummary): number {
  const last = storyboard.scenes[storyboard.scenes.length - 1];
  return last ? last.start_time + last.duration : 0;
}
