"use client";

import { useEffect, useState } from "react";
import { ApiError, getJob, TERMINAL_STATUSES, type JobResponse } from "./api";

const POLL_MS = 1000;
const MAX_BACKOFF_MS = 8000;

export interface JobState {
  job: JobResponse | null;
  /** Client clock (ms) when `job` was received — anchors the elapsed-time ticker. */
  receivedAt: number;
  notFound: boolean;
  /** Several consecutive polls failed; we keep retrying with backoff. */
  connectionLost: boolean;
}

const INITIAL: JobState = { job: null, receivedAt: 0, notFound: false, connectionLost: false };

/**
 * Polls `GET /api/jobs/{id}` about once a second until the job completes or fails.
 * Progress shown in the UI comes only from these responses — nothing is simulated.
 */
export function useJob(jobId: string): JobState {
  const [state, setState] = useState<JobState>(INITIAL);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    let stopped = false;

    const schedule = (ms: number) => {
      if (!stopped) timer = setTimeout(poll, ms);
    };

    async function poll() {
      try {
        const job = await getJob(jobId, controller.signal);
        if (stopped) return;
        failures = 0;
        setState({ job, receivedAt: Date.now(), notFound: false, connectionLost: false });
        if (!TERMINAL_STATUSES.has(job.status)) schedule(POLL_MS);
      } catch (err) {
        if (stopped || (err instanceof DOMException && err.name === "AbortError")) return;
        if (err instanceof ApiError && err.status === 404) {
          setState((s) => ({ ...s, notFound: true, connectionLost: false }));
          return;
        }
        failures += 1;
        if (failures >= 3) setState((s) => (s.connectionLost ? s : { ...s, connectionLost: true }));
        schedule(Math.min(POLL_MS * 2 ** Math.min(failures, 3), MAX_BACKOFF_MS));
      }
    }

    void poll();
    return () => {
      stopped = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  return state;
}
