/**
 * Typed client for the backend HTTP API (see docs/INTERFACES.md, "HTTP API").
 * All requests go to the same origin; Next.js rewrites proxy `/api/*` to FastAPI.
 */

export type JobMode = "live" | "offline_demo";

export type JobStatus =
  | "queued"
  | "planning"
  | "designing"
  | "generating_code"
  | "rendering"
  | "repairing"
  | "validating"
  | "completed"
  | "failed";

export interface StoryboardSceneSummary {
  index: number;
  concept: string;
  caption: string;
  start_time: number;
  duration: number;
}

export interface StoryboardSummary {
  title: string;
  learning_objective: string;
  scenes: StoryboardSceneSummary[];
}

export interface JobResponse {
  job_id: string;
  prompt: string;
  mode: JobMode;
  status: JobStatus;
  stage_index: number;
  progress_stage: string;
  stages: string[];
  detail: string | null;
  error: string | null;
  title: string | null;
  storyboard: StoryboardSummary | null;
  video_url: string | null;
  download_url: string | null;
  duration_seconds: number | null;
  attempts: number | null;
  created_at: string;
  updated_at: string;
  elapsed_seconds: number | null;
}

export interface HealthResponse {
  ok: boolean;
  anthropic_configured: boolean;
  offline_demo_available: boolean;
  planner_model?: string;
  codegen_model?: string;
}

export const PROMPT_MAX_CHARS = 500;

export const TERMINAL_STATUSES: ReadonlySet<JobStatus> = new Set(["completed", "failed"]);

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const NETWORK_MESSAGE =
  "We couldn't reach the server. Check your connection (or that the backend is running) and try again.";

/** FastAPI returns `{detail: string}` or `{detail: [{msg, ...}]}` for validation errors. */
function messageFromBody(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail.trim();
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((item) =>
          item && typeof item === "object" && "msg" in item ? String((item as { msg: unknown }).msg) : "",
        )
        .map((msg) => msg.replace(/^Value error,\s*/i, "").trim())
        .filter(Boolean);
      if (msgs.length) return msgs.join(" ");
    }
  }
  return fallback;
}

function fallbackMessage(status: number): string {
  if (status === 404) return "We couldn't find that.";
  if (status === 422) return "Please check your question and try again.";
  if (status === 503) return "Generation is temporarily unavailable on this server.";
  if (status >= 500) return "Something went wrong on the server. Please try again in a moment.";
  return "Something went wrong. Please try again.";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { cache: "no-store", ...init });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, NETWORK_MESSAGE);
  }
  let body: unknown = null;
  const text = await res.text().catch(() => "");
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!res.ok) {
    // A proxy error page (backend down) comes back as non-JSON 5xx.
    if (res.status >= 500 && body === null) throw new ApiError(res.status, NETWORK_MESSAGE);
    throw new ApiError(res.status, messageFromBody(body, fallbackMessage(res.status)));
  }
  return body as T;
}

export function createJob(prompt: string, mode: JobMode = "live"): Promise<{ job_id: string }> {
  return request<{ job_id: string }>("/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(mode === "live" ? { prompt } : { prompt, mode }),
  });
}

export function getJob(jobId: string, signal?: AbortSignal): Promise<JobResponse> {
  return request<JobResponse>(`/api/jobs/${encodeURIComponent(jobId)}`, { signal });
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health", { signal });
}

export function codeUrl(jobId: string): string {
  return `/api/jobs/${encodeURIComponent(jobId)}/code`;
}
