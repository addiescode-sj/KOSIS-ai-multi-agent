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
      // The admin preview (T3-65) became the operator screens (T3-67)
      { source: "/preview/admin", destination: "/admin/metrics", permanent: false },
      { source: "/preview/admin/filters", destination: "/admin/filters", permanent: false },
      { source: "/preview/admin/:path*", destination: "/admin", permanent: false },
    ];
  },
  // The operator screens read the operator app's /admin/api/* on the same origin (T3-66, T3-67). Deployed, the ingress
  // sends it there before this server sees it. In `next dev`, and in a local `next build` given ADMIN_URL (rewrites are
  // fixed at build time), it is passed to ADMIN_URL here, so the login cookie reaches the operator app the same way.
  async rewrites() {
    const admin = process.env.ADMIN_URL ?? (process.env.NODE_ENV !== "production" ? "http://localhost:8100" : null);
    return admin ? [{ source: "/admin/api/:path*", destination: `${admin}/admin/api/:path*` }] : [];
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
