// In place of a card's chart when the database has no source for it (or nothing in the range): says so and why,
// instead of drawing anything that could be read as a number.
export function NoData({ why, className }: { why: string; className?: string }) {
  return (
    <div className={className ?? "mt-5 rounded-xl bg-canvas p-6 text-center"}>
      <p className="text-sm font-medium text-mute">데이터 없음</p>
      <p className="mt-1 text-xs text-mute">{why}</p>
    </div>
  );
}
