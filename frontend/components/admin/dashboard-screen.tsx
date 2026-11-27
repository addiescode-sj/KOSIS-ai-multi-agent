"use client";

import { useSuspenseQuery } from "@tanstack/react-query";
import { SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { cn } from "cn";

import { Button } from "@/components/ui/button";
import { filterQuery, PERIOD_LABEL } from "@/lib/admin/filter";
import { dashboardQuery } from "@/lib/admin/queries";
import type { AdminFilter } from "@/lib/admin/schemas";

import { ADMIN_HOME, AdminShell } from "./admin-shell";
import { BatchStatus } from "./batch-status";
import { DashboardBody } from "./dashboard-body";
import { FilterBar } from "./filter-bar";

// `/preview/admin`: the metrics dashboard. The server starts the read (app/preview/admin/page.tsx); this picks it
// up with useSuspenseQuery. A new filter changes the address, so the page prefetches the new key.
export function DashboardScreen({ filter }: { filter: AdminFilter }) {
  const { data } = useSuspenseQuery(dashboardQuery(filter));
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const apply = (f: AdminFilter) => startTransition(() => router.replace(`${ADMIN_HOME}?${filterQuery(f)}`, { scroll: false }));
  return (
    <AdminShell
      title="메트릭 대시보드"
      sub={`집계 기준: ${PERIOD_LABEL[filter.period]} · 매시 정각 배치 갱신`}
      status={data.ok ? <BatchStatus {...data.data.batch} /> : undefined}
      actions={
        <Button asChild variant="secondary" size="sm">
          <Link href={`${ADMIN_HOME}/filters?${filterQuery(filter)}`}><SlidersHorizontal />필터 설정</Link>
        </Button>
      }
    >
      <div className={cn("space-y-6 transition-opacity", pending && "opacity-60")} aria-busy={pending}>
        <FilterBar filter={filter} onChange={apply} />
        {data.ok ? (
          <DashboardBody d={data.data} />
        ) : (
          <p role="alert" className="rounded-2xl border border-red-200 bg-red-50 p-6 text-sm text-red-600">
            지표를 불러오지 못했습니다. {data.message}
          </p>
        )}
      </div>
    </AdminShell>
  );
}
