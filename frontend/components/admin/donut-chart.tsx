const R = 70;
const C = 2 * Math.PI * R;
const COLORS = ["#2257e8", "#3b74f6", "#93b4fd", "#bfd3fe", "#dbe7fe"]; // brand 600 → 100, largest first

// Ring of shares with the total in the middle (mockup Donut). The total is the sum of the segments.
export function DonutChart({ segments, sub }: { segments: { label: string; count: number }[]; sub: string }) {
  const total = segments.reduce((a, s) => a + s.count, 0);
  const parts = segments.map((s) => (total ? (s.count / total) * C : 0));
  const starts = parts.map((_, i) => parts.slice(0, i).reduce((a, p) => a + p, 0)); // arc length before each segment
  return (
    <div className="relative mx-auto size-48">
      <svg viewBox="0 0 180 180" className="-rotate-90" role="img" aria-label={segments.map((s) => `${s.label} ${s.count}`).join(", ")}>
        {segments.map((s, i) => (
          <circle
            key={s.label}
            cx="90" cy="90" r={R} fill="none" stroke={COLORS[i % COLORS.length]} strokeWidth="16" strokeLinecap="round"
            strokeDasharray={`${Math.max(0, parts[i] - 4)} ${C}`} strokeDashoffset={-starts[i]}
          />
        ))}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-semibold">{total.toLocaleString("ko-KR")}</span>
        <span className="text-xs text-mute">{sub}</span>
      </div>
    </div>
  );
}
