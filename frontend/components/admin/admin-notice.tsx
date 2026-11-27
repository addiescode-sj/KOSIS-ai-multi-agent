import type { ReactNode } from "react";
import { cn } from "cn";

// A screen's message in place of its data: nothing in the range, a read that failed, a login that ended
export function AdminNotice({ title, text, alert, children }: { title: string; text?: string; alert?: boolean; children?: ReactNode }) {
  return (
    <div role={alert ? "alert" : "status"} className={cn("rounded-2xl border p-6", alert ? "border-red-200 bg-red-50" : "border-line bg-white")}>
      <p className={cn("text-[15px] font-semibold", alert ? "text-red-600" : "text-ink")}>{title}</p>
      {text && <p className="mt-1 text-sm text-sub">{text}</p>}
      {children && <div className="mt-4">{children}</div>}
    </div>
  );
}
