"use client";

import dynamic from "next/dynamic";

// Recharts loads only when an answer has a chart (the home page and refusals do not need it)
export const LazyStatChart = dynamic(() => import("../stat-chart").then((m) => m.StatChart), {
  ssr: false,
  loading: () => <div className="h-[220px] animate-pulse rounded-xl bg-canvas" />,
});
