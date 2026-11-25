import { Lock } from "lucide-react";
import { cn } from "cn";

export function ReadOnlyField({ label, value, missing }: { label: string; value: string; missing?: boolean }) {
  return (
    <div>
      <p className="mb-1.5 text-[13px] font-medium text-sub">{label}</p>
      <div className="flex h-10 items-center justify-between rounded-lg bg-canvas px-3 text-sm text-sub">
        <span className={cn("truncate", missing && "text-mute")}>{value}</span>
        <Lock size={14} className="shrink-0 text-mute" />
      </div>
    </div>
  );
}
