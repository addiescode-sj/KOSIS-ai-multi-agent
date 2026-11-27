"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DEFAULT_FILTER, filterQuery, PERIOD_LABEL, USER_LABEL } from "@/lib/admin/filter";
import type { AdminFilter } from "@/lib/admin/schemas";

import { ADMIN_METRICS } from "./admin-shell";
import { SelectField } from "./select-field";
import { ToggleField } from "./toggle-field";

// The common metrics filter (mockup Filters). Starts from the address; 적용 goes back to the dashboard with it.
export function FilterForm({ initial }: { initial: AdminFilter }) {
  const router = useRouter();
  const [f, setF] = useState(initial);
  return (
    <Card className="max-w-xl space-y-5 p-6">
      <div>
        <SelectField label="기간" value={f.period} options={PERIOD_LABEL} onChange={(period) => setF({ ...f, period })} />
        <p className="mt-1.5 text-xs text-mute">기본값은 {PERIOD_LABEL[DEFAULT_FILTER.period]}입니다.</p>
      </div>
      <SelectField label="사용자 구분" value={f.user} options={USER_LABEL} onChange={(user) => setF({ ...f, user })} />
      <div className="rounded-xl bg-canvas p-4">
        <ToggleField label="내부 트래픽 포함" checked={f.internal} onChange={(internal) => setF({ ...f, internal })} />
        <p className="mt-2 text-xs text-mute">팀원 테스트 요청을 집계에 포함합니다. 단계별 지연 집계에도 같은 설정이 적용됩니다.</p>
      </div>
      <div className="flex justify-end gap-2 border-t border-line pt-5">
        <Button variant="secondary" onClick={() => setF(DEFAULT_FILTER)}>기본값으로 되돌리기</Button>
        <Button onClick={() => router.push(`${ADMIN_METRICS}?${filterQuery(f)}`)}>필터 적용</Button>
      </div>
    </Card>
  );
}
