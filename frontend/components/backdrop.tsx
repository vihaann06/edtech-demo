/** Ambient page background: soft colour glows over a faint coordinate plane. */
export function Backdrop() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div className="absolute inset-0 bg-ink-900" />
      <div className="bg-plane absolute inset-0 [mask-image:radial-gradient(ellipse_75%_60%_at_50%_0%,black_10%,transparent_75%)]" />
      <div className="absolute -top-48 left-1/2 h-[560px] w-[1100px] max-w-[160vw] -translate-x-1/2 rounded-full bg-[radial-gradient(closest-side,rgb(88_196_221/0.16),transparent)] blur-2xl" />
      <div className="absolute -top-24 left-[62%] h-[420px] w-[620px] max-w-[120vw] -translate-x-1/2 rounded-full bg-[radial-gradient(closest-side,rgb(179_148_246/0.14),transparent)] blur-2xl" />
      <div className="absolute bottom-[-30%] left-[10%] h-[520px] w-[720px] max-w-[140vw] rounded-full bg-[radial-gradient(closest-side,rgb(92_208_179/0.06),transparent)] blur-3xl" />
      <div className="absolute inset-x-0 bottom-0 h-64 bg-gradient-to-t from-ink-950/80 to-transparent" />
    </div>
  );
}
