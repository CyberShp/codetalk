import type { NextConfig } from "next";

// Intentionally NOT using `output: "standalone"`.
// Standalone mode is designed for Docker images / serverless cold starts,
// neither of which apply to our intranet-PC deployment. Keeping it
// previously caused two production bugs:
//   1. `.next/standalone/` does not include `.next/static/` or `public/`
//      (Next.js docs say these should be served by a CDN), so every
//      `/_next/static/*` returned 404 and the app rendered unstyled.
//   2. Standalone bakes a snapshot of server code; when the source drifted
//      from the last build, hydration crashed (e.g. Sidebar items mismatch).
// We use plain `next start` instead — one build artifact, no copy step,
// no drift between standalone snapshot and source.
const nextConfig: NextConfig = {
  ...(process.env.CODETALK_NEXT_DIST_DIR
    ? { distDir: process.env.CODETALK_NEXT_DIST_DIR }
    : {}),
  allowedDevOrigins: ["127.0.0.1"],
};

export default nextConfig;

