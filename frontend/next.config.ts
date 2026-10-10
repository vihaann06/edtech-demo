import path from "node:path";
import type { NextConfig } from "next";

// Where the FastAPI backend lives. The browser only ever talks to this Next.js
// origin; `/api/*` is proxied to the backend (including HTTP Range requests for
// video seeking). `||` (not `??`) so an empty `BACKEND_URL=` falls back too.
// Note: rewrites are resolved when the server starts (`next dev`) or at build
// time (`next build`), so set BACKEND_URL before running either.
const backendUrl = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  poweredByHeader: false,
  turbopack: {
    // Pin the workspace root (a stray lockfile higher up the tree would otherwise be picked).
    root: path.join(__dirname),
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
