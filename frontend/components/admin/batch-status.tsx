import { cn } from "cn";

// The metrics batch state in the admin header: green when the last hourly run succeeded
export function BatchStatus({ ok, updatedAt }: { ok: boolean; updatedAt: string }) {
  return (
    <span className={cn("inline-flex h-7 items-center gap-1.5 rounded-full px-3 text-xs font-medium", ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600")}>
      <span className={cn("size-1.5 rounded-full", ok ? "bg-emerald-500" : "bg-red-500")} />
      {ok ? "배치 정상" : "배치 실패"} · {updatedAt} 갱신
    </span>
  );
}
