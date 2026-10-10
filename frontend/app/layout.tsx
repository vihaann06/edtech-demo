import type { Metadata, Viewport } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { Providers } from "@/components/providers";
import { Backdrop } from "@/components/backdrop";
import { SiteHeader } from "@/components/site-header";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Lumen — Any idea, beautifully explained",
    template: "%s · Lumen",
  },
  description: "Turn a question into a 30-second animated explanation.",
  applicationName: "Lumen",
};

export const viewport: Viewport = {
  themeColor: "#080b16",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable} h-full antialiased`}>
      <body className="relative flex min-h-full flex-col">
        <Providers>
          <a
            href="#main"
            className="sr-only z-50 rounded-lg bg-fg px-3 py-2 text-sm font-medium text-ink-950 focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
          >
            Skip to content
          </a>
          <Backdrop />
          <SiteHeader />
          <main id="main" className="relative flex flex-1 flex-col">
            {children}
          </main>
        </Providers>
      </body>
    </html>
  );
}
