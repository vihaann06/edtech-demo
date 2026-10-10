import type { Metadata } from "next";
import { Suspense } from "react";
import { ExplainView } from "@/components/explain/explain-view";
import { ExplainSkeleton } from "@/components/explain/explain-skeleton";

export const metadata: Metadata = {
  title: "Your explanation",
};

export default function ExplainPage({ params }: PageProps<"/explain/[jobId]">) {
  return (
    <Suspense fallback={<ExplainSkeleton />}>
      {params.then(({ jobId }) => (
        <ExplainView key={jobId} jobId={jobId} />
      ))}
    </Suspense>
  );
}
