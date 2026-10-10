import { WifiOff } from "lucide-react";

/** Shown while the route params resolve and before the first job poll returns. */
export function ExplainSkeleton({ connectionLost = false }: { connectionLost?: boolean }) {
  return (
    <div className="mx-auto w-full max-w-7xl px-4 pb-16 pt-4 sm:px-6 sm:pt-6 lg:px-8" aria-busy="true">
      {connectionLost ? (
        <p role="status" className="mb-6 inline-flex items-center gap-2 rounded-full bg-amber/10 px-3 py-1.5 text-sm text-amber">
          <WifiOff className="size-4" aria-hidden />
          We can&apos;t reach the server right now — retrying…
        </p>
      ) : (
        <span className="sr-only">Loading your explanation…</span>
      )}
      <div aria-hidden>
        <div className="h-4 w-48 rounded-full bg-white/[0.06]" />
        <div className="mt-5 h-9 w-full max-w-2xl rounded-xl bg-white/[0.06]" />
        <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px] lg:gap-8">
          <div className="relative aspect-video overflow-hidden rounded-2xl border border-line bg-ink-950/70">
            <div className="absolute inset-0 animate-shimmer bg-[linear-gradient(90deg,transparent,rgb(255_255_255/0.03),transparent)]" />
          </div>
          <div className="hidden rounded-2xl border border-line bg-white/[0.015] lg:block" />
        </div>
      </div>
    </div>
  );
}
