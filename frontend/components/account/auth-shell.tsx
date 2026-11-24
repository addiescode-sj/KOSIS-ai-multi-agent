import { ChartNoAxesColumn, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "cn";

// The centered card of the login, logged-out and withdrawn screens (prototype Auth Shell)
export function AuthShell({ icon: Icon = ChartNoAxesColumn, tone = "bg-brand-600 text-white", children }: {
  icon?: LucideIcon;
  tone?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas p-6">
      <div className="w-full max-w-[420px] rounded-2xl border border-line bg-white p-10 text-center shadow-[0_8px_30px_rgba(17,24,39,0.06)]">
        <span className={cn("mx-auto flex size-12 items-center justify-center rounded-full", tone)}>
          <Icon size={22} strokeWidth={2.25} />
        </span>
        {children}
      </div>
    </div>
  );
}
