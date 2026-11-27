"use client";

import { Search } from "lucide-react";
import { useState } from "react";

import { count, dotDate } from "@/lib/admin/format";
import type { AdminDashboard } from "@/lib/admin/schemas";

import { DataTable } from "./data-table";
import { Panel } from "./panel";

// Uses per statistics table, narrowed by the search box (table or survey name)
export function TableUsage({ tables }: { tables: AdminDashboard["tables"] }) {
  const [q, setQ] = useState("");
  const words = q.trim();
  const shown = words ? tables.filter((t) => t.name.includes(words) || t.survey.includes(words)) : tables;
  return (
    <Panel
      title="표별 사용 횟수"
      action={
        <label className="flex h-9 w-60 items-center gap-2 rounded-lg border border-line px-3 text-sm">
          <Search size={15} className="text-mute" />
          <input value={q} onChange={(e) => setQ(e.target.value)} className="w-full outline-none" placeholder="통계표 검색" aria-label="통계표 검색" />
        </label>
      }
    >
      <div className="mt-4">
        <DataTable
          right={[2, 3]}
          cols={["통계표명", "조사명", "사용 횟수", "최근 사용일"]}
          rows={shown.map((t) => [t.name, t.survey, count(t.uses), dotDate(t.lastUsed)])}
        />
        {shown.length === 0 && <p className="py-6 text-center text-sm text-mute">‘{words}’와 일치하는 통계표가 없어요.</p>}
      </div>
    </Panel>
  );
}
