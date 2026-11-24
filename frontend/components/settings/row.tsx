import type { ReactNode } from "react";
import { cn } from "cn";

export function Row({ label, hint, last, children }: { label: string; hint?: ReactNode; last?: boolean; children: ReactNode }) {
  return (
    <div className={cn("flex items-center justify-between gap-6 py-4", !last && "border-b border-line")}>
      <div className="min-w-0">
        <p className="text-sm font-medium">{label}</p>
        {hint && <p className="mt-0.5 text-[13px] text-sub">{hint}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}
