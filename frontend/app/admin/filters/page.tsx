import { Suspense } from "react";

import { AdminGate } from "@/components/admin/admin-gate";
import { AdminShell } from "@/components/admin/admin-shell";
import { FilterForm } from "@/components/admin/filter-form";
import { DEFAULT_FILTER, filterFrom } from "@/lib/admin/filter";

// The common metrics filter: starts from the filter in the address (the dashboard links here with it)
export default function Page({ searchParams }: PageProps<"/admin/filters">) {
  return (
    <AdminGate>
      <AdminShell title="메트릭 필터 설정" sub="모든 메트릭 탭에 공통으로 적용되는 조건입니다.">
        <Suspense fallback={<FilterForm initial={DEFAULT_FILTER} />}>
          {searchParams.then((p) => {
            const f = filterFrom(p);
            return <FilterForm key={JSON.stringify(f)} initial={f} />;
          })}
        </Suspense>
      </AdminShell>
    </AdminGate>
  );
}
