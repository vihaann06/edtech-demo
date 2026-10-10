import Link from "next/link";
import { ArrowLeft, SearchX } from "lucide-react";
import { buttonClasses } from "@/components/ui/button";

export function NotFoundView({
  title = "We couldn't find that explanation",
  message = "The link may be mistyped, or the server may have restarted since this video was made.",
}: {
  title?: string;
  message?: string;
}) {
  return (
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col items-center px-4 pb-16 pt-16 text-center sm:pt-[14vh]">
      <div className="grid size-12 place-items-center rounded-2xl bg-white/[0.04] text-fg-muted ring-1 ring-line-strong">
        <SearchX className="size-5" aria-hidden />
      </div>
      <h1 className="mt-6 text-2xl font-semibold tracking-[-0.02em] text-fg">{title}</h1>
      <p className="mt-2 text-[15px] leading-relaxed text-fg-muted">{message}</p>
      <Link href="/" className={buttonClasses("primary", "md", "mt-8")}>
        <ArrowLeft className="size-4" aria-hidden />
        Create a new explanation
      </Link>
    </div>
  );
}
