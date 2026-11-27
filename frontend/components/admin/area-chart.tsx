"use client";

import { useId } from "react";

const W = 600;
const H = 200;

// A filled line over dashed guides with the last point marked (mockup AreaChart). Pure SVG, scaled to the width.
export function AreaChart({ data, labels, height = 200 }: { data: number[]; labels?: string[]; height?: number }) {
  const gradient = useId();
  const max = Math.max(...data) * 1.15 || 1;
  const pts = data.map((v, i) => [data.length > 1 ? (i / (data.length - 1)) * W : W / 2, H - (v / max) * H] as const);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x},${y}`).join(" ");
  const last = pts[pts.length - 1];
  return (
    <div>
      <div className="relative" style={{ height }}>
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="absolute inset-0 size-full overflow-visible" aria-hidden>
          <defs>
            <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#3b74f6" stopOpacity=".22" />
              <stop offset="1" stopColor="#3b74f6" stopOpacity="0" />
            </linearGradient>
          </defs>
          {[0.25, 0.5, 0.75].map((g) => (
            <line key={g} x1="0" x2={W} y1={H * g} y2={H * g} stroke="#e7eaf0" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />
          ))}
          <path d={`${line} L${W},${H} L0,${H} Z`} fill={`url(#${gradient})`} />
          <path d={line} fill="none" stroke="#2257e8" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        </svg>
        {last && (
          <div
            className="absolute size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-brand-600 bg-white"
            style={{ left: `${(last[0] / W) * 100}%`, top: `${(last[1] / H) * 100}%` }}
          />
        )}
      </div>
      {labels && (
        <div className="mt-3 flex justify-between text-xs text-mute">
          {labels.map((l) => <span key={l}>{l}</span>)}
        </div>
      )}
    </div>
  );
}
