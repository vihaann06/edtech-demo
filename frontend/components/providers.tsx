"use client";

import { MotionConfig } from "motion/react";
import type { ReactNode } from "react";

/** Client-side providers. Motion animations honour `prefers-reduced-motion`. */
export function Providers({ children }: { children: ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}
