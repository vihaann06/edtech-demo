import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export type ButtonVariant = "primary" | "secondary" | "ghost";
export type ButtonSize = "sm" | "md" | "lg";

const base =
  "relative inline-flex select-none items-center justify-center gap-2 whitespace-nowrap rounded-xl font-medium " +
  "transition-[background-color,border-color,color,box-shadow,transform,opacity] duration-200 ease-out " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink-900 " +
  "active:scale-[0.98] disabled:pointer-events-none disabled:active:scale-100";

const variants: Record<ButtonVariant, string> = {
  primary:
    "text-ink-950 bg-[linear-gradient(100deg,#7fe3f0_0%,#58c4dd_30%,#8a93f7_70%,#b394f6_100%)] " +
    "shadow-[0_0_0_1px_rgb(255_255_255/0.12)_inset,0_8px_28px_-10px_rgb(88_196_221/0.65)] " +
    "hover:shadow-[0_0_0_1px_rgb(255_255_255/0.25)_inset,0_10px_36px_-8px_rgb(138_147_247/0.75)] hover:brightness-110 " +
    "disabled:bg-none disabled:bg-white/[0.07] disabled:text-fg-faint disabled:shadow-none",
  secondary:
    "border border-line-strong bg-white/[0.05] text-fg hover:border-white/25 hover:bg-white/[0.09] " +
    "disabled:text-fg-faint",
  ghost: "text-fg-muted hover:bg-white/[0.06] hover:text-fg disabled:text-fg-faint",
};

const sizes: Record<ButtonSize, string> = {
  sm: "h-9 px-3 text-sm",
  md: "h-10 px-4 text-sm",
  lg: "h-12 px-5 text-[15px]",
};

export function buttonClasses(variant: ButtonVariant = "secondary", size: ButtonSize = "md", className?: string) {
  return cn(base, variants[variant], sizes[size], className);
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", className, type = "button", ...props },
  ref,
) {
  return <button ref={ref} type={type} className={buttonClasses(variant, size, className)} {...props} />;
});
