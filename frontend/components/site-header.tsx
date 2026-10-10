import Link from "next/link";
import { Wordmark } from "./brand";

export function SiteHeader() {
  return (
    <header className="relative z-20">
      <div className="mx-auto flex h-16 w-full max-w-7xl items-center px-4 sm:px-6 lg:px-8">
        <Link
          href="/"
          aria-label="Lumen home"
          className="-mx-1.5 rounded-lg px-1.5 py-1 transition-opacity hover:opacity-85 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue/60"
        >
          <Wordmark />
        </Link>
      </div>
    </header>
  );
}
