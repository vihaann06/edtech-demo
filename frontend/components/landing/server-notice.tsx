"use client";

import { FlaskConical, KeyRound, PlugZap } from "lucide-react";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

type ServerNoticeProps =
  | {
      kind: "not-configured";
      offlineDemoAvailable: boolean;
      pending: boolean;
      disabled: boolean;
      onRunDemo: () => void;
      className?: string;
    }
  | { kind: "unreachable"; className?: string };

export function ServerNotice(props: ServerNoticeProps) {
  if (props.kind === "unreachable") {
    return (
      <div
        role="status"
        className={cn(
          "mx-auto flex max-w-xl items-start gap-3 rounded-2xl border border-line bg-white/[0.02] px-4 py-3 text-sm text-fg-muted",
          props.className,
        )}
      >
        <PlugZap className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden />
        <p>We can&apos;t reach the generation server right now. It may still be starting up — try again in a moment.</p>
      </div>
    );
  }

  const { offlineDemoAvailable, pending, disabled, onRunDemo, className } = props;
  return (
    <div
      role="status"
      className={cn(
        "flex flex-col gap-3 rounded-2xl border border-amber/25 bg-[linear-gradient(100deg,rgb(246_194_107/0.09),rgb(246_194_107/0.04))] px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:gap-5",
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-amber/15 text-amber">
          <KeyRound className="size-3.5" aria-hidden />
        </span>
        <div className="text-sm leading-relaxed">
          <p className="text-fg">
            AI generation isn&apos;t configured on this server{" "}
            <span className="whitespace-nowrap text-fg-muted">
              (set <code className="rounded bg-white/[0.06] px-1 py-px font-mono text-[12.5px]">ANTHROPIC_API_KEY</code>).
            </span>
          </p>
          {offlineDemoAvailable && (
            <p className="mt-0.5 text-[13px] text-fg-subtle">
              You can still render the offline demo — a pre-written sample lesson, not AI-generated.
            </p>
          )}
        </div>
      </div>
      {offlineDemoAvailable && (
        <Button variant="secondary" size="sm" onClick={onRunDemo} disabled={disabled} className="shrink-0 self-start sm:self-center">
          {pending ? <Spinner /> : <FlaskConical className="size-4" aria-hidden />}
          Render offline demo
        </Button>
      )}
    </div>
  );
}
