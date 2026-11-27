"use client";

import { useQuery } from "@tanstack/react-query";
import { SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { cn } from "cn";

import { Button } from "@/components/ui/button";
import { filterQuery } from "@/lib/admin/filter";
import { dashboardQuery, DashboardFailure, type DashboardResult } from "@/lib/admin/queries";
import type { AdminFilter } from "@/lib/admin/schemas";

import { ADMIN_HOME, AdminShell } from "./admin-shell";
import { DashboardBody } from "./dashboard-body";
import { FilterBar } from "./filter-bar";
import { ReadStatus } from "./read-status";

// `/preview/admin`: the metrics dashboard over the operator app's real numbers (T3-66). Read in the browser only
// (the operator app sees the login cookie on the same origin; no server prefetch). A new filter changes the address.
export function DashboardScreen({ filter }: { filter: AdminFilter }) {
  const { data, isPending } = useQuery(dashboardQuery(filter));
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const apply = (f: AdminFilter) => startTransition(() => router.replace(`${ADMIN_HOME}?${filterQuery(f)}`, { scroll: false }));
  const range = data?.ok ? `${data.data.range.start} ~ ${data.data.range.end}` : null;
  return (
    <AdminShell
      title="메트릭 대시보드"
      sub={range ? `집계 기간: ${range} (KST, 어제까지)` : "운영자 앱의 지표(log_metrics)를 읽습니다"}
      status={data?.ok ? <ReadStatus readAt={data.data.readAt} /> : undefined}
      actions={
        <Button asChild variant="secondary" size="sm">
          <Link href={`${ADMIN_HOME}/filters?${filterQuery(filter)}`}><SlidersHorizontal />필터 설정</Link>
        </Button>
      }
    >
      <div className={cn("space-y-6 transition-opacity", (pending || isPending) && "opacity-60")} aria-busy={pending || isPending}>
        <FilterBar filter={filter} onChange={apply} />
        {data?.ok && data.data.notice && <p role="status" className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">{data.data.notice}</p>}
        {isPending ? <p className="text-sm text-mute">지표를 불러오는 중…</p> : data && <Body result={data} />}
      </div>
    </AdminShell>
  );
}

function Body({ result }: { result: DashboardResult }) {
  if (result.ok) return <DashboardBody d={result.data} />;
  if (result.failure === DashboardFailure.LOGIN) {
    return (
      <Notice title="운영자 로그인이 필요합니다" text="카카오로 로그인한 뒤, 운영자 허용 목록에 있는 계정이면 지표가 보입니다.">
        <Button asChild size="sm"><a href={`/login?next=${encodeURIComponent(ADMIN_HOME)}`}>로그인</a></Button>
      </Notice>
    );
  }
  if (result.failure === DashboardFailure.FORBIDDEN) {
    return <Notice title="볼 수 없는 화면입니다" text="운영자 허용 목록에 있는 계정만 지표를 볼 수 있습니다." />;
  }
  return <Notice title="지표를 불러오지 못했습니다" text={`${result.message} 운영자 앱(admin/)이 켜져 있는지 확인해 주세요.`} alert />;
}

function Notice({ title, text, alert, children }: { title: string; text: string; alert?: boolean; children?: React.ReactNode }) {
  return (
    <div role={alert ? "alert" : "status"} className={cn("rounded-2xl border p-6", alert ? "border-red-200 bg-red-50" : "border-line bg-white")}>
      <p className={cn("text-[15px] font-semibold", alert ? "text-red-600" : "text-ink")}>{title}</p>
      <p className="mt-1 text-sm text-sub">{text}</p>
      {children && <div className="mt-4">{children}</div>}
    </div>
  );
}
