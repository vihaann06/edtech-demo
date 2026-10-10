import { FlaskConical } from "lucide-react";
import { cn } from "@/lib/cn";

/** Must be shown on every screen that displays an offline-demo job. */
export function DemoBadge({ className }: { className?: string }) {
  return (
    <div
      role="note"
      className={cn(
        "inline-flex items-start gap-2 rounded-xl border border-amber/30 bg-amber/[0.09] px-3 py-2 text-[13px] leading-snug text-amber",
        className,
      )}
    >
      <FlaskConical className="mt-px size-4 shrink-0" aria-hidden />
      <span>
        <strong className="font-semibold">Offline demo</strong>
        <span className="text-amber/85"> — pre-written sample lesson, not AI-generated</span>
      </span>
    </div>
  );
}
