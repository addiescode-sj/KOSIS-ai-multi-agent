"use client";

import { useState } from "react";

import { Chip } from "@/components/ui/chip";
import { PERIOD_LABEL, USER_LABEL } from "@/lib/admin/filter";
import type { AdminFilter } from "@/lib/admin/schemas";

import { SelectField } from "./select-field";
import { ToggleField } from "./toggle-field";

const TABS = ["요약", "답 품질", "사용 패턴", "성능·비용"] as const;

// Dashboard tabs and the quick filter row. Tabs only mark the chosen view (one page of cards in the mockup);
// the filter goes to the address through onChange, so the numbers are read again for it.
export function FilterBar({ filter, onChange }: { filter: AdminFilter; onChange: (f: AdminFilter) => void }) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("요약");
  return (
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div className="flex gap-2" role="tablist" aria-label="지표 묶음">
        {TABS.map((t) => (
          <Chip key={t} role="tab" aria-selected={tab === t} active={tab === t} onClick={() => setTab(t)}>{t}</Chip>
        ))}
      </div>
      <div className="flex items-center gap-3">
        <SelectField className="block w-36" value={filter.period} options={PERIOD_LABEL} onChange={(period) => onChange({ ...filter, period })} />
        <SelectField className="block w-36" value={filter.user} options={USER_LABEL} onChange={(user) => onChange({ ...filter, user })} />
        <ToggleField label="내부 트래픽 포함" checked={filter.internal} onChange={(internal) => onChange({ ...filter, internal })} />
      </div>
    </div>
  );
}
