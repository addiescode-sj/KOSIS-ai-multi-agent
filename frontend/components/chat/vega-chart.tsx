"use client";

import { useEffect, useRef, useState } from "react";

// Draws the backend's Vega-Lite spec (make_chart, from phase 1 facts) as it is. Only the look is set here
// (config: brand colours, font, no border); data, encodings and numbers stay the backend's.
const CONFIG = {
  font: "Pretendard, Inter, system-ui, sans-serif",
  background: "transparent",
  view: { stroke: null },
  mark: { color: "#2257e8" },
  range: { category: ["#2257e8", "#93b4fd", "#1b45c4", "#bfd3fe", "#3b74f6", "#dbe7fe"] },
  axis: { labelColor: "#9ca3af", titleColor: "#6b7280", gridColor: "#e7eaf0", domainColor: "#e7eaf0", tickColor: "#e7eaf0" },
  legend: { labelColor: "#6b7280", titleColor: "#6b7280" },
  title: { color: "#111827", fontSize: 14, fontWeight: 600, anchor: "start" },
};

export function VegaChart({ spec }: { spec: Record<string, unknown> }) {
  const ref = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let finalize: (() => void) | undefined;
    let cancelled = false;
    import("vega-embed")
      .then(({ default: embed }) =>
        embed(ref.current!, { width: "container", ...spec } as never, { actions: false, renderer: "svg", config: CONFIG as never }),
      )
      .then((result) => {
        if (cancelled) result.finalize();
        else finalize = () => result.finalize();
      })
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
      finalize?.();
    };
  }, [spec]);

  if (failed) return <p className="text-[13px] text-mute">차트를 그리지 못했습니다.</p>;
  return <div ref={ref} className="w-full" />;
}
