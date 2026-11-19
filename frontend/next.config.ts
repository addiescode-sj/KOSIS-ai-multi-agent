import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Minimal server bundle for the Docker image (see frontend/Dockerfile)
  output: "standalone",
  cacheComponents: true,
  partialPrefetching: true,
  // Routes renamed in PR #32 review: keep old links working
  async redirects() {
    return [
      { source: "/c/:id", destination: "/chat/:id", permanent: true },
      { source: "/conversations", destination: "/chat", permanent: true },
    ];
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
