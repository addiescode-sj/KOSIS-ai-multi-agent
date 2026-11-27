import { cn } from "cn";

const DAYS = ["월", "화", "수", "목", "금", "토", "일"];
const HOURS = ["0", "3", "6", "9", "12", "15", "18", "21"];
const SHADE = ["bg-brand-50", "bg-brand-100", "bg-brand-200", "bg-brand-300", "bg-brand-500", "bg-brand-600"];

// Questions by weekday × three-hour slot, each cell one of six levels (mockup Heat)
export function Heatmap({ levels }: { levels: number[][] }) {
  return (
    <div className="grid grid-cols-[28px_repeat(8,1fr)] gap-1.5 text-[11px] text-mute">
      {DAYS.map((d, di) => [
        <span key={d} className="flex items-center">{d}</span>,
        ...HOURS.map((h, hi) => {
          const level = levels[di]?.[hi] ?? 0;
          return <div key={`${d}${h}`} title={`${d} ${h}시 · ${level}단계`} className={cn("h-7 rounded-md", SHADE[Math.min(5, Math.max(0, level))])} />;
        }),
      ])}
      <span />
      {HOURS.map((h) => <span key={h} className="text-center">{h}시</span>)}
    </div>
  );
}
