"use client";

import { useLayoutEffect, type ReactNode, type RefObject } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, CircleAlert, CornerDownLeft, FlaskConical } from "lucide-react";
import { PROMPT_MAX_CHARS } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import type { SubmitError } from "./landing";

const COUNTER_FROM = 400;
const MAX_TEXTAREA_PX = 240;

interface ComposerProps {
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  pending: boolean;
  disabled: boolean;
  /** Increments each time the user tries to submit an empty prompt. */
  nudge: number;
  error: SubmitError | null;
  offlineDemoAvailable: boolean;
  onRunDemo: () => void;
}

export function Composer({
  textareaRef,
  value,
  onChange,
  onSubmit,
  pending,
  disabled,
  nudge,
  error,
  offlineDemoAvailable,
  onRunDemo,
}: ComposerProps) {
  const empty = value.trim().length === 0;
  const length = value.length;

  // Grow with the content, up to a comfortable maximum.
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_PX)}px`;
  }, [value, textareaRef]);

  return (
    <div>
      <div className="group relative">
        <div
          aria-hidden
          className="pointer-events-none absolute -inset-3 rounded-[32px] bg-[linear-gradient(100deg,rgb(88_196_221/0.35),rgb(138_147_247/0.28),rgb(179_148_246/0.35))] opacity-25 blur-2xl transition-opacity duration-500 group-focus-within:opacity-60"
        />
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSubmit();
          }}
          className={cn(
            "relative rounded-[22px] border bg-ink-850/85 shadow-[0_1px_0_0_rgb(255_255_255/0.06)_inset,0_24px_60px_-24px_rgb(0_0_0/0.8)] backdrop-blur-xl transition-colors duration-300",
            error ? "border-rose/40" : "border-line-strong group-focus-within:border-blue/45",
          )}
        >
          <label htmlFor="prompt" className="sr-only">
            Your question
          </label>
          <textarea
            id="prompt"
            ref={textareaRef}
            value={value}
            rows={2}
            maxLength={PROMPT_MAX_CHARS}
            autoFocus
            autoComplete="off"
            spellCheck
            placeholder="What do you want to understand?"
            aria-describedby="composer-hint"
            aria-invalid={error ? true : undefined}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                onSubmit();
              }
            }}
            className="block min-h-[92px] w-full resize-none bg-transparent px-5 pb-2 pt-5 text-[17px] leading-relaxed text-fg placeholder:text-fg-faint focus:outline-none sm:min-h-[104px] sm:px-6 sm:pt-6 sm:text-lg"
          />

          <div className="flex flex-col gap-3 px-3 pb-3 sm:flex-row sm:items-center sm:justify-between sm:pl-6">
            <div
              id="composer-hint"
              className={cn("min-h-5 px-2 text-[13px] leading-5 sm:block sm:px-0", !empty && "hidden")}
              aria-live="polite"
            >
              {empty ? (
                <motion.p
                  key={nudge}
                  initial={nudge ? { x: 0 } : false}
                  animate={nudge ? { x: [0, -5, 5, -3, 3, 0] } : undefined}
                  transition={{ duration: 0.4 }}
                  className={cn("transition-colors", nudge ? "text-amber" : "text-fg-subtle")}
                >
                  {nudge
                    ? "Type a question first — or pick one of the examples below."
                    : "Ask about any concept, or try an example below."}
                </motion.p>
              ) : (
                <p className="hidden items-center gap-1.5 text-fg-subtle sm:flex">
                  <Kbd>
                    <CornerDownLeft className="size-3" aria-hidden />
                    <span className="sr-only">Enter</span>
                  </Kbd>
                  to generate
                  <span className="mx-1 text-fg-faint">·</span>
                  <Kbd>Shift</Kbd>
                  <Kbd>
                    <CornerDownLeft className="size-3" aria-hidden />
                    <span className="sr-only">Enter</span>
                  </Kbd>
                  new line
                </p>
              )}
            </div>

            <div className="flex items-center gap-3">
              {length >= COUNTER_FROM && (
                <span
                  className={cn(
                    "ml-2 font-mono text-xs tabular-nums sm:ml-0",
                    length >= PROMPT_MAX_CHARS ? "text-rose" : length >= 470 ? "text-amber" : "text-fg-subtle",
                  )}
                  aria-label={`${length} of ${PROMPT_MAX_CHARS} characters used`}
                >
                  {length}/{PROMPT_MAX_CHARS}
                </span>
              )}
              <Button
                type="submit"
                variant="primary"
                size="lg"
                disabled={empty || disabled}
                className="w-full rounded-[14px] sm:w-auto"
              >
                {pending ? (
                  <>
                    <Spinner />
                    Starting…
                  </>
                ) : (
                  <>
                    Generate Explanation
                    <ArrowRight className="size-4" aria-hidden />
                  </>
                )}
              </Button>
            </div>
          </div>
        </form>
      </div>

      <AnimatePresence initial={false}>
        {error && (
          <motion.div
            key="error"
            role="alert"
            initial={{ opacity: 0, y: -6, height: 0 }}
            animate={{ opacity: 1, y: 0, height: "auto" }}
            exit={{ opacity: 0, y: -6, height: 0 }}
            transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
          >
            <div className="mt-3 flex flex-col gap-3 rounded-2xl border border-rose/25 bg-rose/[0.07] px-4 py-3 text-sm text-fg sm:flex-row sm:items-center sm:justify-between">
              <p className="flex items-start gap-2.5 leading-relaxed">
                <CircleAlert className="mt-0.5 size-4 shrink-0 text-rose" aria-hidden />
                <span>{error.message}</span>
              </p>
              {error.status === 503 && offlineDemoAvailable && (
                <Button variant="secondary" size="sm" onClick={onRunDemo} disabled={disabled} className="shrink-0">
                  <FlaskConical className="size-4" aria-hidden />
                  Render offline demo
                </Button>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded-md border border-line-strong bg-white/[0.04] px-1 font-sans text-[11px] font-medium text-fg-muted">
      {children}
    </kbd>
  );
}
