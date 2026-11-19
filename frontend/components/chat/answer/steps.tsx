import { cn } from "cn";

import { STEPS } from "@/lib/chat/answer";

export function Steps({ done }: { done: number }) {
  return (
    <div className="space-y-2" aria-label={`${STEPS.length}단계 중 ${done}단계 완료`}>
      <div className="flex gap-1.5">
        {STEPS.map((s, i) => (
          <div key={s} className={cn("h-1.5 flex-1 rounded-full", i < done ? "bg-brand-600" : "bg-brand-100")} />
        ))}
      </div>
      <div className="flex justify-between text-xs text-mute">
        {STEPS.map((s, i) => (
          <span key={s} className={cn(i < done && "font-medium text-brand-700")}>{s}</span>
        ))}
      </div>
    </div>
  );
}
