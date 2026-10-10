"use client";

import { ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/cn";

export const EXAMPLE_PROMPTS = [
  "How does self-attention work in Transformers?",
  "Explain gradient descent visually.",
  "How does binary search work?",
  "Explain how neural networks learn.",
] as const;

export function ExamplePrompts({
  onChoose,
  disabled,
  className,
}: {
  onChoose: (prompt: string) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <section aria-labelledby="examples-heading" className={cn("flex flex-col items-center", className)}>
      <h2 id="examples-heading" className="mb-3 text-xs font-medium uppercase tracking-[0.14em] text-fg-subtle">
        Try an example
      </h2>
      <ul className="flex w-full flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-center">
        {EXAMPLE_PROMPTS.map((example) => (
          <li key={example}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onChoose(example)}
              className={cn(
                "group/chip flex w-full items-center justify-between gap-2 rounded-xl border border-line bg-white/[0.025] px-3.5 py-2.5 text-left text-sm text-fg-muted",
                "transition-[background-color,border-color,color] duration-200",
                "hover:border-blue/35 hover:bg-blue/[0.06] hover:text-fg",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue/60",
                "disabled:pointer-events-none disabled:opacity-50 sm:w-auto sm:rounded-full sm:py-2",
              )}
            >
              <span>{example}</span>
              <ArrowUpRight
                className="size-3.5 shrink-0 text-fg-faint transition-[color,transform] duration-200 group-hover/chip:-translate-y-px group-hover/chip:translate-x-px group-hover/chip:text-blue"
                aria-hidden
              />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
