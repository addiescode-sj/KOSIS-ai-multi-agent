import { MessageSquare, ShieldX, Target, ThumbsUp, TrendingDown, TrendingUp, type LucideIcon } from "lucide-react";
import { cn } from "cn";

import { signed, withUnit } from "@/lib/admin/format";
import type { Stat } from "@/lib/admin/schemas";

const ICON: Record<Stat["key"], LucideIcon> = {
  questions: MessageSquare,
  refusal_rate: ShieldX,
  accuracy: Target,
  thumbs_up_rate: ThumbsUp,
};

// One headline number. The arrow follows the sign of the change, the color whether that change is good.
export function StatCard({ stat }: { stat: Stat }) {
  const Icon = ICON[stat.key];
  const Arrow = stat.delta < 0 ? TrendingDown : TrendingUp;
  const tone = stat.good ? "text-emerald-600" : "text-red-500";
  return (
    <div className="p-5">
      <span className="flex size-9 items-center justify-center rounded-full border border-brand-100 bg-brand-50 text-brand-600">
        <Icon size={16} />
      </span>
      <p className="mt-3 text-[13px] text-sub">{stat.label}</p>
      <p className="mt-1 text-[28px] font-semibold tracking-tight">{withUnit(stat.value, stat.unit)}</p>
      <p className="mt-1 flex items-center gap-1 text-xs">
        <Arrow size={13} className={tone} />
        <span className={cn("font-medium", tone)}>{signed(stat.delta, stat.deltaUnit)}</span>
        <span className="text-mute">{stat.note}</span>
      </p>
    </div>
  );
}
