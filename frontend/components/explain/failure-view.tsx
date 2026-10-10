"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { motion } from "motion/react";
import { CircleAlert, RotateCcw } from "lucide-react";
import { ApiError, createJob, type JobResponse } from "@/lib/api";
import { Button, buttonClasses } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { DemoBadge } from "@/components/demo-badge";

export function FailureView({ job }: { job: JobResponse }) {
  const router = useRouter();
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const demo = job.mode === "offline_demo";
  const stoppedAt = job.stages?.[job.stage_index] ?? job.progress_stage;

  async function tryAgain() {
    setRetrying(true);
    setRetryError(null);
    try {
      const { job_id } = await createJob(job.prompt, job.mode);
      router.push(`/explain/${encodeURIComponent(job_id)}`);
    } catch (err) {
      setRetryError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
      setRetrying(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-xl flex-1 flex-col px-4 pb-16 pt-8 sm:px-6 sm:pt-[10vh]">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        className="relative overflow-hidden rounded-3xl border border-line bg-ink-850/75 p-6 shadow-[0_30px_80px_-40px_rgb(0_0_0/0.9)] backdrop-blur-xl sm:p-8"
      >
        <div
          aria-hidden
          className="pointer-events-none absolute -top-24 left-1/2 h-48 w-80 -translate-x-1/2 rounded-full bg-[radial-gradient(closest-side,rgb(245_138_138/0.16),transparent)] blur-2xl"
        />
        {demo && <DemoBadge className="relative mb-5" />}
        <div className="relative grid size-11 place-items-center rounded-2xl bg-rose/10 text-rose ring-1 ring-rose/25">
          <CircleAlert className="size-5" aria-hidden />
        </div>
        <h1 className="relative mt-5 text-2xl font-semibold tracking-[-0.02em] text-fg">
          We couldn&apos;t finish this explanation
        </h1>
        <p className="relative mt-2 text-[15px] leading-relaxed text-fg-muted" role="alert">
          {job.error || "Something went wrong while generating your video."}
        </p>

        <dl className="relative mt-6 space-y-3 rounded-2xl border border-line bg-white/[0.02] p-4 text-sm">
          <div>
            <dt className="text-xs uppercase tracking-[0.12em] text-fg-subtle">{demo ? "Prompt" : "Your question"}</dt>
            <dd className="mt-1 line-clamp-4 break-words text-fg">{job.prompt}</dd>
          </div>
          {stoppedAt && (
            <div>
              <dt className="text-xs uppercase tracking-[0.12em] text-fg-subtle">Stopped during</dt>
              <dd className="mt-1 text-fg-muted">{stoppedAt}</dd>
            </div>
          )}
        </dl>

        <div className="relative mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Link href="/" className={buttonClasses("secondary", "md")}>
            Start over
          </Link>
          <Button variant="primary" onClick={tryAgain} disabled={retrying}>
            {retrying ? <Spinner /> : <RotateCcw className="size-4" aria-hidden />}
            Try again
          </Button>
        </div>
        {retryError && (
          <p className="relative mt-3 text-sm text-rose" role="alert">
            {retryError}
          </p>
        )}
      </motion.div>
    </div>
  );
}
