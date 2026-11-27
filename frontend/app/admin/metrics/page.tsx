import { Suspense } from "react";

import { AdminGate } from "@/components/admin/admin-gate";
import { DashboardScreen } from "@/components/admin/dashboard-screen";
import { filterFrom } from "@/lib/admin/filter";

// The metrics dashboard (T3-65, real numbers since T3-66): the filter from the address, the numbers read in the browser
export default function Page({ searchParams }: PageProps<"/admin/metrics">) {
  return (
    <AdminGate>
      <Suspense>
        {searchParams.then((p) => <DashboardScreen filter={filterFrom(p)} />)}
      </Suspense>
    </AdminGate>
  );
}
