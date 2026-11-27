import type { ReactNode } from "react";
import { cn } from "cn";

// A plain admin table (mockup Table): the first column in ink, the rest in sub; `right` columns are numbers
export function DataTable({ cols, rows, right = [] }: { cols: string[]; rows: ReactNode[][]; right?: number[] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-line text-left text-xs text-mute">
          {cols.map((c, i) => <th key={c} className={cn("pb-2 font-medium", right.includes(i) && "text-right")}>{c}</th>)}
        </tr>
      </thead>
      <tbody className="divide-y divide-line">
        {rows.map((r, ri) => (
          <tr key={ri}>
            {r.map((c, i) => (
              <td key={i} className={cn("py-2.5", i === 0 ? "text-ink" : "text-sub", right.includes(i) && "text-right tabular-nums")}>{c}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
