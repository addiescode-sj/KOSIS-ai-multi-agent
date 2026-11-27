const KST_TIME = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", hour12: false });

// That the numbers are real and stop at yesterday, and when the operator app read them (it keeps them 5 minutes)
export function ReadStatus({ readAt }: { readAt: string }) {
  return (
    <span className="inline-flex h-7 items-center gap-1.5 rounded-full bg-emerald-50 px-3 text-xs font-medium text-emerald-700">
      <span className="size-1.5 rounded-full bg-emerald-500" />
      실제 지표 · 어제까지 · {KST_TIME.format(new Date(readAt))} 조회
    </span>
  );
}
