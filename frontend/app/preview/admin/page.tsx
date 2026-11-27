import { Suspense } from "react";

import { AdminShell } from "@/components/admin/admin-shell";
import { DashboardScreen } from "@/components/admin/dashboard-screen";
import { filterFrom } from "@/lib/admin/filter";

// The metrics dashboard. The shell is prerendered (fallback); the numbers are read in the browser from the operator
// app on the same origin (T3-66 option B: the login cookie goes there, never through this server), so nothing is
// prefetched here.
export default function Page({ searchParams }: PageProps<"/preview/admin">) {
  return (
    <Suspense fallback={<Loading />}>
      {searchParams.then((p) => <DashboardScreen filter={filterFrom(p)} />)}
    </Suspense>
  );
}

function Loading() {
  return (
    <AdminShell title="메트릭 대시보드" sub="지표를 불러오는 중">
      <p className="text-sm text-mute">불러오는 중…</p>
    </AdminShell>
  );
}
