import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Minimal server bundle for the Docker image (see frontend/Dockerfile)
  output: "standalone",
  // The Kakao callback address holds the login code: keep it out of the dev server log (T3-54)
  logging: { incomingRequests: { ignore: [/^\/auth\//]} },
  cacheComponents: true,
  partialPrefetching: true,
  experimental: {
    agentUpgrade: "security",
  },
  // Routes renamed in PR #32 review: keep old links working
  async redirects() {
    return [
      { source: "/c/:id", destination: "/chat/:id", permanent: true },
      { source: "/conversations", destination: "/chat", permanent: true },
    ];
  },
  // The admin preview (T3-63, sample numbers) is for `next dev` only: in a production server its address goes to a
  // route that does not exist, so it answers a real 404 before any of it renders (the layout guard is the backup).
  async rewrites() {
    if (process.env.NODE_ENV !== "production") return [];
    return {
      beforeFiles: [{ source: "/preview/admin/:path*", destination: "/__admin-preview-off" }],
      afterFiles: [],
      fallback: [],
    };
  },
  turbopack: {
    // Repo root has its own package-lock.json (harness scripts); pin the app root here
    root: __dirname,
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
