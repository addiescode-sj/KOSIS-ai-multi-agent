"use client";

import { useQuery } from "@tanstack/react-query";
import { SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { cn } from "cn";

import { Button } from "@/components/ui/button";
import { filterQuery } from "@/lib/admin/filter";
import { dashboardQuery } from "@/lib/admin/queries";
import type { AdminFilter } from "@/lib/admin/schemas";

import { ADMIN_METRICS, AdminShell } from "./admin-shell";
import { DashboardBody } from "./dashboard-body";
import { FilterBar } from "./filter-bar";
import { ReadFailure } from "./read-failure";
import { ReadStatus } from "./read-status";

// `/admin/metrics`: the metrics dashboard over the operator app's real numbers (T3-66). Read in the browser only
// (the operator app sees the login cookie on the same origin; no server prefetch). A new filter changes the address.
export function DashboardScreen({ filter }: { filter: AdminFilter }) {
  const { data, isPending } = useQuery(dashboardQuery(filter));
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const apply = (f: AdminFilter) => startTransition(() => router.replace(`${ADMIN_METRICS}?${filterQuery(f)}`, { scroll: false }));
  const range = data?.ok ? `${data.data.range.start} ~ ${data.data.range.end}` : null;
  return (
    <AdminShell
      title="메트릭 대시보드"
      sub={range ? `집계 기간: ${range} (KST, 어제까지)` : "운영자 앱의 지표(log_metrics)를 읽습니다"}
      status={data?.ok ? <ReadStatus readAt={data.data.readAt} /> : undefined}
      actions={
        <Button asChild variant="secondary" size="sm">
          <Link href={`/admin/filters?${filterQuery(filter)}`}><SlidersHorizontal />필터 설정</Link>
        </Button>
      }
    >
      <div className={cn("space-y-6 transition-opacity", (pending || isPending) && "opacity-60")} aria-busy={pending || isPending}>
        <FilterBar filter={filter} onChange={apply} />
        {data?.ok && data.data.notice && <p role="status" className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">{data.data.notice}</p>}
        {isPending ? <p className="text-sm text-mute">지표를 불러오는 중…</p> : data && (data.ok ? <DashboardBody d={data.data} /> : <ReadFailure {...data} />)}
      </div>
    </AdminShell>
  );
}
