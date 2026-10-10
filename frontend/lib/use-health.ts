"use client";

import { useEffect, useState } from "react";
import { getHealth, type HealthResponse } from "./api";

export type HealthState =
  | { status: "loading" }
  | { status: "ok"; data: HealthResponse }
  | { status: "unreachable" };

/** Fetches `GET /api/health` once per mount (and again when an Activity-hidden page reappears). */
export function useHealth(): HealthState {
  const [state, setState] = useState<HealthState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal)
      .then((data) => setState({ status: "ok", data }))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setState({ status: "unreachable" });
      });
    return () => controller.abort();
  }, []);

  return state;
}
