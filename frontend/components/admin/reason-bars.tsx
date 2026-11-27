import { count, share } from "@/lib/admin/format";
import type { Labeled } from "@/lib/admin/schemas";

// Each reason with its count and its share of all reasons (computed here, not stored)
export function ReasonBars({ rows }: { rows: Labeled[] }) {
  const total = rows.reduce((a, r) => a + r.count, 0);
  return (
    <div className="mt-5 space-y-3">
      {rows.map((r) => {
        const p = share(r.count, total);
        return (
          <div key={r.label}>
            <div className="mb-1 flex justify-between text-[13px]">
              <span>{r.label}</span>
              <span className="text-sub tabular-nums">{count(r.count)} · {p}</span>
            </div>
            <div className="h-2 rounded-full bg-brand-50">
              <div className="h-2 rounded-full bg-brand-500" style={{ width: p }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
