import { dehydrate, HydrationBoundary } from "@tanstack/react-query";
import { connection } from "next/server";
import { Suspense } from "react";

import { AdminShell } from "@/components/admin/admin-shell";
import { DashboardScreen } from "@/components/admin/dashboard-screen";
import { filterFrom } from "@/lib/admin/filter";
import { prefetchDashboard } from "@/lib/admin/read.server";
import type { AdminFilter } from "@/lib/admin/schemas";
import { makeQueryClient } from "@/lib/query/client";

// The metrics dashboard. Like /search: the shell is prerendered (fallback), per request the server starts the read
// for the address's filter without waiting and hands the pending query to the browser (useSuspenseQuery).
export default function Page({ searchParams }: PageProps<"/preview/admin">) {
  return (
    <Suspense fallback={<Loading />}>
      {searchParams.then((p) => <DashboardData filter={filterFrom(p)} />)}
    </Suspense>
  );
}

async function DashboardData({ filter }: { filter: AdminFilter }) {
  await connection(); // QueryClient and dehydrate() read the current time: request time only
  const client = makeQueryClient();
  prefetchDashboard(client, filter);
  return (
    <HydrationBoundary state={dehydrate(client)}>
      <Suspense fallback={<Loading />}>
        <DashboardScreen filter={filter} />
      </Suspense>
    </HydrationBoundary>
  );
}

function Loading() {
  return (
    <AdminShell title="메트릭 대시보드" sub="지표를 불러오는 중">
      <p className="text-sm text-mute">불러오는 중…</p>
    </AdminShell>
  );
}
