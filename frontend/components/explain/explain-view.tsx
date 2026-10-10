"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { CircleCheck, CodeXml, Download, Info, Plus, RotateCcw, Sparkles, WifiOff } from "lucide-react";
import { codeUrl, type JobResponse } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDuration, formatTimestamp, sceneIndexFromDetail, secondsBetween } from "@/lib/format";
import { useJob } from "@/lib/use-job";
import { Button, buttonClasses } from "@/components/ui/button";
import { DemoBadge } from "@/components/demo-badge";
import { CanvasLoader } from "./canvas-loader";
import { ChapterList } from "./chapter-list";
import { ElapsedClock } from "./elapsed";
import { ExplainSkeleton } from "./explain-skeleton";
import { FailureView } from "./failure-view";
import { LessonPlan } from "./lesson-plan";
import { NotFoundView } from "./not-found-view";
import { StageList } from "./stage-list";

/** Mirrors backend STAGE_LABELS; only used if a response ever omits `stages`. */
const DEFAULT_STAGES = [
  "Understanding your concept",
  "Designing your explanation",
  "Creating animations",
  "Rendering your video",
  "Your explanation is ready",
];

const EASE = [0.22, 1, 0.36, 1] as const;

export function ExplainView({ jobId }: { jobId: string }) {
  const { job, receivedAt, notFound, connectionLost } = useJob(jobId);

  const docTitle = notFound
    ? "Not found"
    : !job
      ? null
      : job.status === "completed"
        ? (job.storyboard?.title ?? job.title ?? "Your explanation")
        : job.status === "failed"
          ? "Generation stopped"
          : "Generating…";
  useEffect(() => {
    if (docTitle) document.title = `${docTitle} · Lumen`;
  }, [docTitle]);

  if (notFound) return <NotFoundView />;
  if (!job) return <ExplainSkeleton connectionLost={connectionLost} />;
  if (job.status === "failed") return <FailureView job={job} />;
  return <JobView job={job} receivedAt={receivedAt} connectionLost={connectionLost} />;
}

function JobView({
  job,
  receivedAt,
  connectionLost,
}: {
  job: JobResponse;
  receivedAt: number;
  connectionLost: boolean;
}) {
  const completed = job.status === "completed";
  const demo = job.mode === "offline_demo";
  const stages = job.stages?.length ? job.stages : DEFAULT_STAGES;
  const stageIndex = Math.max(0, Math.min(stages.length - 1, job.stage_index ?? 0));
  const storyboard = job.storyboard;
  const scenes = storyboard?.scenes ?? [];
  const videoUrl = job.video_url ?? `/api/jobs/${encodeURIComponent(job.job_id)}/video`;
  const downloadUrl = job.download_url ?? `${videoUrl}?download=1`;

  const videoRef = useRef<HTMLVideoElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const wasGenerating = useRef(false);
  const [currentTime, setCurrentTime] = useState(0);

  // When generation finishes while the user is scrolled down, bring the video into view.
  useEffect(() => {
    if (!completed) {
      wasGenerating.current = true;
      return;
    }
    if (!wasGenerating.current) return;
    const frame = frameRef.current;
    if (!frame) return;
    const rect = frame.getBoundingClientRect();
    if (rect.top < 0 || rect.bottom > window.innerHeight) {
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      frame.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
    }
  }, [completed]);

  // Cache Components keeps this route mounted-but-hidden after navigating away: stop playback.
  useEffect(() => {
    const video = videoRef.current;
    return () => video?.pause();
  }, [completed]);

  function seek(seconds: number) {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = seconds;
    setCurrentTime(seconds);
    void video.play().catch(() => undefined);
  }

  const heading = demo
    ? (storyboard?.title ?? "Offline demo lesson")
    : completed
      ? (storyboard?.title ?? job.title ?? job.prompt)
      : job.prompt;
  const madeIn = completed ? (secondsBetween(job.created_at, job.updated_at) ?? job.elapsed_seconds) : null;
  const duration = job.duration_seconds ?? (scenes.length ? scenes[scenes.length - 1].start_time + scenes[scenes.length - 1].duration : null);
  const showAside = !completed || scenes.length > 0;

  return (
    <div className="mx-auto w-full max-w-7xl px-4 pb-16 pt-2 sm:px-6 sm:pt-4 lg:px-8">
      <p className="sr-only" aria-live="polite">
        {completed
          ? "Your explanation is ready."
          : `Step ${stageIndex + 1} of ${stages.length}: ${stages[stageIndex]}.`}
      </p>

      {/* Status row */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1 pt-1.5 text-sm text-fg-muted">
          {completed ? (
            <span className="inline-flex items-center gap-2 font-medium text-teal">
              <CircleCheck className="size-4" aria-hidden />
              {demo ? "Offline demo ready" : "Your explanation is ready"}
            </span>
          ) : (
            <span className="inline-flex items-center gap-2 font-medium text-fg">
              <span aria-hidden className="relative flex size-2">
                <span className="absolute inset-0 animate-ping rounded-full bg-blue/60" />
                <span className="relative size-2 rounded-full bg-blue" />
              </span>
              {demo ? "Rendering the offline demo" : "Generating your explanation"}
            </span>
          )}
          <span aria-hidden className="hidden text-fg-faint sm:inline">
            ·
          </span>
          {completed ? (
            <span>
              {duration ? `${formatTimestamp(duration)} video` : "Video"}
              {madeIn ? ` · made in ${formatDuration(madeIn)}` : ""}
            </span>
          ) : (
            <span>
              <ElapsedClock baseSeconds={job.elapsed_seconds ?? 0} receivedAt={receivedAt} /> elapsed
              {!demo && <span className="text-fg-subtle"> · usually takes 2–4 minutes</span>}
            </span>
          )}
          {connectionLost && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-amber/10 px-2 py-0.5 text-xs text-amber">
              <WifiOff className="size-3" aria-hidden />
              Reconnecting…
            </span>
          )}
        </div>
        <Link href="/" className={buttonClasses("ghost", "sm", "-mr-2 shrink-0")}>
          <Plus className="size-4" aria-hidden />
          <span className="hidden sm:inline">New explanation</span>
          <span className="sm:hidden">New</span>
        </Link>
      </div>

      {demo && <DemoBadge className="mt-4" />}

      <motion.h1
        key={heading}
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: EASE }}
        title={heading.length > 140 ? heading : undefined}
        className="mt-3 line-clamp-3 max-w-4xl text-balance break-words text-[1.7rem] font-semibold leading-[1.15] tracking-[-0.025em] text-fg sm:text-4xl"
      >
        {heading}
      </motion.h1>
      {completed && storyboard?.learning_objective && (
        <motion.p
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.08, ease: EASE }}
          className="mt-3 max-w-3xl text-pretty text-base leading-relaxed text-fg-muted sm:text-lg"
        >
          {storyboard.learning_objective}
        </motion.p>
      )}

      <div
        className={cn(
          "mt-6 grid gap-5 sm:mt-7 lg:gap-8",
          showAside && "lg:grid-cols-[minmax(0,1fr)_340px] xl:grid-cols-[minmax(0,1fr)_372px]",
        )}
      >
        <div className="min-w-0">
          <div
            ref={frameRef}
            className={cn(
              "relative aspect-video overflow-hidden rounded-2xl border bg-ink-950 sm:rounded-[20px]",
              "shadow-[0_40px_100px_-40px_rgb(88_196_221/0.35),0_0_0_1px_rgb(255_255_255/0.02)]",
              completed ? "border-line-strong" : "border-line",
            )}
          >
            <AnimatePresence initial={false}>
              {completed ? (
                <motion.video
                  key="video"
                  ref={videoRef}
                  src={videoUrl}
                  controls
                  playsInline
                  autoPlay
                  muted
                  preload="auto"
                  aria-label={`Animated explanation: ${heading}`}
                  onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
                  onSeeked={(e) => setCurrentTime(e.currentTarget.currentTime)}
                  initial={{ opacity: 0, scale: 1.01 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ duration: 0.7, ease: EASE }}
                  className="absolute inset-0 size-full bg-black object-contain"
                />
              ) : (
                <motion.div
                  key="loader"
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.5 }}
                  className="absolute inset-0"
                >
                  <CanvasLoader stageIndex={stageIndex} />
                  <FrameCaption label={stages[stageIndex]} detail={job.detail} repairing={job.status === "repairing"} />
                  <StageScrubber stageCount={stages.length - 1} stageIndex={stageIndex} />
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {completed && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.15, ease: EASE }}
              className="mt-4 grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center"
            >
              <Button variant="secondary" onClick={() => seek(0)}>
                <RotateCcw className="size-4" aria-hidden />
                Replay
              </Button>
              <a href={downloadUrl} download className={buttonClasses("secondary", "md")}>
                <Download className="size-4" aria-hidden />
                Download MP4
              </a>
              <Link href="/" className={buttonClasses("primary", "md", "col-span-2 sm:ml-auto")}>
                <Sparkles className="size-4" aria-hidden />
                Generate Another
              </Link>
            </motion.div>
          )}
        </div>

        {showAside && (
          <aside className="min-w-0">
            {completed ? (
              <Panel title="Chapters" meta={`${scenes.length} scenes`} bodyClassName="p-2">
                <ChapterList scenes={scenes} currentTime={currentTime} onSeek={seek} />
              </Panel>
            ) : (
              <Panel title="Progress" meta={`Step ${Math.min(stageIndex + 1, stages.length)} of ${stages.length}`}>
                <StageList
                  stages={stages}
                  stageIndex={stageIndex}
                  detail={job.detail}
                  repairing={job.status === "repairing"}
                />
              </Panel>
            )}
            {!completed && (
              <p className="mt-4 flex items-start gap-2.5 px-1 text-[13px] leading-relaxed text-fg-subtle">
                <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                {demo
                  ? "The offline demo skips the AI steps and renders a pre-written lesson. The video will appear here when it's ready."
                  : "Every video is planned, animated and rendered from scratch for your question. It will appear right here when it's ready."}
              </p>
            )}
          </aside>
        )}
      </div>

      {!completed && (
        <LessonPlan
          storyboard={storyboard}
          showTitle={!demo}
          activeScene={stageIndex === 2 ? sceneIndexFromDetail(job.detail) : null}
          className="mt-10 sm:mt-12"
        />
      )}

      {completed && (
        <footer className="mt-12 flex flex-col gap-3 border-t border-line pt-6 text-sm sm:flex-row sm:items-start sm:justify-between sm:gap-8">
          <p className="min-w-0 break-words text-fg-subtle">
            <span className="text-fg-subtle">{demo ? "Your prompt" : "You asked"} · </span>
            <span className="text-fg-muted">{job.prompt}</span>
          </p>
          <a
            href={codeUrl(job.job_id)}
            target="_blank"
            rel="noreferrer"
            className="inline-flex shrink-0 items-center gap-1.5 rounded-md text-fg-subtle transition-colors hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue/60"
          >
            <CodeXml className="size-4" aria-hidden />
            {demo ? "View scene code" : "View generated code"}
          </a>
        </footer>
      )}
    </div>
  );
}

function Panel({
  title,
  meta,
  children,
  bodyClassName,
}: {
  title: string;
  meta?: string;
  children: ReactNode;
  bodyClassName?: string;
}) {
  return (
    <section className="rounded-2xl border border-line bg-ink-850/60 backdrop-blur-sm" aria-label={title}>
      <header className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
        <h2 className="text-sm font-medium text-fg">{title}</h2>
        {meta && <span className="text-xs tabular-nums text-fg-subtle">{meta}</span>}
      </header>
      <div className={cn("p-5", bodyClassName)}>{children}</div>
    </section>
  );
}

/** The current stage, styled like the burned-in captions of the finished video. */
function FrameCaption({ label, detail, repairing }: { label: string; detail: string | null; repairing: boolean }) {
  return (
    <div className="absolute inset-x-0 bottom-0 flex flex-col items-center bg-gradient-to-t from-ink-950/95 via-ink-950/70 to-transparent px-4 pb-5 pt-12 text-center sm:pb-9">
      <AnimatePresence mode="wait" initial={false}>
        <motion.p
          key={label}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.35 }}
          className="text-[15px] font-medium tracking-[-0.01em] text-fg sm:text-xl"
        >
          {label}
        </motion.p>
      </AnimatePresence>
      <AnimatePresence mode="wait" initial={false}>
        {detail && (
          <motion.p
            key={detail}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }}
            className={cn("mt-1 text-xs sm:text-sm", repairing ? "text-amber" : "text-fg-muted")}
          >
            {detail}
          </motion.p>
        )}
      </AnimatePresence>
    </div>
  );
}

/**
 * Segmented bar along the bottom edge of the frame: one segment per working stage.
 * Finished stages are filled; the current one shimmers (indeterminate) — never a fake percentage.
 */
function StageScrubber({ stageCount, stageIndex }: { stageCount: number; stageIndex: number }) {
  return (
    <div aria-hidden className="absolute inset-x-0 bottom-0 flex gap-1 px-1 pb-1">
      {Array.from({ length: stageCount }, (_, i) => (
        <div key={i} className="relative h-[3px] flex-1 overflow-hidden rounded-full bg-white/[0.08]">
          {i < stageIndex && <div className="absolute inset-0 bg-gradient-to-r from-blue to-indigo" />}
          {i === stageIndex && (
            <div className="absolute inset-0 animate-shimmer bg-[linear-gradient(90deg,transparent,#58c4dd,transparent)]" />
          )}
        </div>
      ))}
    </div>
  );
}
