import { cn } from "@/lib/cn";

/** Lumen mark: a bell curve with a point of light at its peak. */
export function LogoMark({ className, idSuffix = "a" }: { className?: string; idSuffix?: string }) {
  const gradientId = `lumen-mark-${idSuffix}`;
  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden className={cn("shrink-0", className)}>
      <defs>
        <linearGradient id={gradientId} x1="4" y1="6" x2="28" y2="26" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#7fe3f0" />
          <stop offset="0.5" stopColor="#58c4dd" />
          <stop offset="1" stopColor="#b394f6" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="30" height="30" rx="9" fill="#0c1020" />
      <rect x="1.5" y="1.5" width="29" height="29" rx="8.5" stroke={`url(#${gradientId})`} strokeOpacity="0.55" />
      <path
        d="M5.5 22.5c4.2 0 6.3-11.5 10.5-11.5s6.3 11.5 10.5 11.5"
        stroke={`url(#${gradientId})`}
        strokeWidth="2.4"
        strokeLinecap="round"
      />
      <circle cx="16" cy="11" r="5" fill="#f4d345" opacity="0.18" />
      <circle cx="16" cy="11" r="2.6" fill="#f4d345" />
    </svg>
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <LogoMark className="size-7" idSuffix="header" />
      <span className="text-[17px] font-semibold tracking-[-0.01em] text-fg">Lumen</span>
    </span>
  );
}
