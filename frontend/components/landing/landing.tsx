"use client";

import { useRouter } from "next/navigation";
import { useLayoutEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { Sparkles } from "lucide-react";
import { ApiError, createJob, type JobMode } from "@/lib/api";
import { useHealth } from "@/lib/use-health";
import { Composer } from "./composer";
import { ExamplePrompts } from "./example-prompts";
import { ServerNotice } from "./server-notice";

/** Used when the offline demo is started with an empty input (the API requires a prompt). */
const OFFLINE_DEMO_PROMPT = "Offline demo";

export interface SubmitError {
  message: string;
  status: number;
}

const fadeUp = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0 },
};

export function Landing() {
  const router = useRouter();
  const health = useHealth();
  const [prompt, setPrompt] = useState("");
  const [pending, setPending] = useState<JobMode | null>(null);
  const [error, setError] = useState<SubmitError | null>(null);
  const [nudge, setNudge] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const submittedRef = useRef(false);

  // Cache Components keeps this page mounted-but-hidden after navigating away.
  // Focus the input whenever it becomes visible, and start fresh after a successful submit
  // (so "Generate another" lands on an empty, focused composer).
  useLayoutEffect(() => {
    textareaRef.current?.focus({ preventScroll: true });
    return () => {
      if (submittedRef.current) {
        submittedRef.current = false;
        setPrompt("");
        setPending(null);
        setError(null);
        setNudge(0);
      }
    };
  }, []);

  const offlineDemoAvailable = health.status === "ok" && health.data.offline_demo_available;
  const aiConfigured = health.status !== "ok" || health.data.anthropic_configured;

  async function submit(mode: JobMode) {
    if (pending) return;
    const text = prompt.trim();
    if (mode === "live" && !text) {
      setNudge((n) => n + 1);
      textareaRef.current?.focus();
      return;
    }
    setPending(mode);
    setError(null);
    try {
      const { job_id } = await createJob(text || OFFLINE_DEMO_PROMPT, mode);
      submittedRef.current = true;
      router.push(`/explain/${encodeURIComponent(job_id)}`);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? { message: err.message, status: err.status }
          : { message: "Something went wrong. Please try again.", status: 0 },
      );
      setPending(null);
    }
  }

  function chooseExample(example: string) {
    setPrompt(example);
    setError(null);
    setNudge(0);
    const el = textareaRef.current;
    if (el) {
      el.focus();
      requestAnimationFrame(() => el.setSelectionRange(example.length, example.length));
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 pb-10 pt-6 sm:px-6 sm:pt-[9vh]">
      <motion.div
        initial="hidden"
        animate="show"
        transition={{ staggerChildren: 0.07 }}
        className="flex flex-col items-center text-center"
      >
        <motion.div
          variants={fadeUp}
          transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
          className="mb-6 inline-flex items-center gap-2 rounded-full border border-line bg-white/[0.03] py-1 pl-2 pr-3 text-[13px] text-fg-muted backdrop-blur-sm"
        >
          <span className="grid size-5 place-items-center rounded-full bg-blue/15 text-blue">
            <Sparkles className="size-3" aria-hidden />
          </span>
          Animated explainers, generated on demand
        </motion.div>

        <motion.h1
          variants={fadeUp}
          transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
          className="text-balance text-[2.6rem] font-semibold leading-[1.04] tracking-[-0.035em] text-fg sm:text-6xl md:text-7xl"
        >
          Any idea.
          <br />
          <span className="text-gradient">Beautifully explained.</span>
        </motion.h1>

        <motion.p
          variants={fadeUp}
          transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
          className="mt-5 max-w-xl text-pretty text-[17px] leading-relaxed text-fg-muted sm:text-lg"
        >
          Turn a question into a 30-second animated explanation.
        </motion.p>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 18 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.7, delay: 0.22, ease: [0.22, 1, 0.36, 1] }}
        className="mt-9 sm:mt-11"
      >
        {!aiConfigured && (
          <ServerNotice
            kind="not-configured"
            offlineDemoAvailable={offlineDemoAvailable}
            pending={pending === "offline_demo"}
            disabled={pending !== null}
            onRunDemo={() => submit("offline_demo")}
            className="mb-4"
          />
        )}

        <Composer
          textareaRef={textareaRef}
          value={prompt}
          onChange={(value) => {
            setPrompt(value);
            if (error) setError(null);
          }}
          onSubmit={() => submit("live")}
          pending={pending === "live"}
          disabled={pending !== null}
          nudge={nudge}
          error={error}
          offlineDemoAvailable={offlineDemoAvailable}
          onRunDemo={() => submit("offline_demo")}
        />

        <ExamplePrompts onChoose={chooseExample} disabled={pending !== null} className="mt-7" />

        {health.status === "unreachable" && <ServerNotice kind="unreachable" className="mt-8" />}
      </motion.div>
    </div>
  );
}
