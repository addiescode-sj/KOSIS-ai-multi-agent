"use client";

import { Search } from "lucide-react";
import { useState } from "react";

import { count } from "@/lib/admin/format";
import type { DashboardView } from "@/lib/admin/view";

import { DataTable } from "./data-table";
import { NoData } from "./no-data";
import { Panel } from "./panel";

// Answers per statistics table (log_metrics top_tables: answered turns, top 20), narrowed by the search box
export function TableUsage({ tables }: { tables: DashboardView["tables"] }) {
  const [q, setQ] = useState("");
  const words = q.trim();
  const shown = words ? tables.filter((t) => t.key.includes(words) || t.survey.includes(words)) : tables;
  return (
    <Panel
      title="표별 사용 횟수"
      sub="답에 쓴 통계표, 상위 20개"
      action={
        <label className="flex h-9 w-60 items-center gap-2 rounded-lg border border-line px-3 text-sm">
          <Search size={15} className="text-mute" />
          <input value={q} onChange={(e) => setQ(e.target.value)} className="w-full outline-none" placeholder="표 키·조사명 검색" aria-label="통계표 검색" />
        </label>
      }
    >
      {tables.length === 0 ? (
        <NoData why="이 기간에 답한 질문이 없습니다." />
      ) : (
        <div className="mt-4">
          <DataTable right={[2]} cols={["통계표 키", "조사명", "답한 횟수"]} rows={shown.map((t) => [t.key, t.survey, count(t.uses)])} />
          {shown.length === 0 && <p className="py-6 text-center text-sm text-mute">‘{words}’와 일치하는 통계표가 없어요.</p>}
        </div>
      )}
    </Panel>
  );
}
