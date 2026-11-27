// A small grey box with a label over a value (mockup's LLM usage boxes; `tight`: the TTFT percentile boxes)
export function MetricTile({ label, value, tight }: { label: string; value: string; tight?: boolean }) {
  return (
    <div className="rounded-xl bg-canvas p-3">
      <p className="text-xs text-mute">{label}</p>
      <p className={tight ? "text-lg font-semibold" : "mt-1 text-lg font-semibold"}>{value}</p>
    </div>
  );
}
