import { cn } from "cn";

import { count } from "@/lib/admin/format";
import type { Labeled } from "@/lib/admin/view";

// Vertical bars with the count above and the label below (mockup BarChart); `highlight` is the bar drawn darker
export function BarChart({ data, height = 180, highlight }: { data: Labeled[]; height?: number; highlight?: number }) {
  const max = Math.max(1, ...data.map((d) => d.count));
  return (
    <div className="flex items-end gap-3" style={{ height }} role="img" aria-label={data.map((d) => `${d.label} ${count(d.count)}`).join(", ")}>
      {data.map((d, i) => (
        <div key={d.label} className="flex h-full flex-1 flex-col items-center justify-end gap-2">
          <span className="text-[11px] font-medium text-sub">{count(d.count)}</span>
          <div className={cn("w-full max-w-12 rounded-md", i === highlight ? "bg-brand-600" : "bg-brand-200")} style={{ height: `${(d.count / max) * 75}%` }} />
          <span className="text-xs text-mute">{d.label}</span>
        </div>
      ))}
    </div>
  );
}
