import { ArrowRight, ExternalLink, Loader2, Table2 } from "lucide-react";

import { Card } from "@/components/ui/card";
import { mergeTables, type TablesResult } from "@/lib/search/tables";

import { Highlight } from "./highlight";

// KOSIS tables for q. `tables` undefined = still searching (the Suspense fallback).
export function TableSection({ tables, q, wide }: { tables: TablesResult | undefined; q: string; wide?: boolean }) {
  const list = tables?.ok ? mergeTables(tables.out) : [];
  return (
    <section>
      <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-sub"><Table2 size={16} />KOSIS 통계표 <span className="text-mute">{list.length}</span></h2>
      {!tables && <Card className="flex items-center gap-2 px-5 py-8 text-sm text-sub"><Loader2 size={16} className="animate-spin text-brand-600" />통계표를 찾고 있습니다…</Card>}
      {tables && !tables.ok && <Card className="px-5 py-8 text-center text-sm text-red-600">{tables.message}</Card>}
      {tables?.ok && !list.length && <Card className="px-5 py-8 text-center text-sm text-sub">{tables.out.refusal_text ?? "맞는 통계표가 없어요."}</Card>}
      <div className={wide ? "grid gap-3 md:grid-cols-2" : "grid gap-3"}>
        {list.map((t) => (
          <Card key={t.key} className="p-4">
            <div className="flex items-start justify-between gap-3">
              <p className="text-[15px] leading-snug font-semibold"><Highlight text={t.table_name} q={q} /></p>
              <a href={t.url} target="_blank" rel="noreferrer" className="shrink-0 rounded-md p-1 text-mute hover:bg-gray-100 hover:text-brand-600" aria-label="KOSIS에서 원표 보기"><ExternalLink size={15} /></a>
            </div>
            <p className="mt-1 text-xs text-mute">KOSIS / {t.survey_name} / {t.organization}</p>
            <p className="mt-0.5 text-xs text-mute">수록기간 {t.period}</p>
            <a href={`/?q=${encodeURIComponent(t.table_name)}`} className="mt-3 inline-flex items-center gap-1 text-[13px] font-medium text-brand-600 hover:underline">
              이 표로 질문하기 <ArrowRight size={13} />
            </a>
          </Card>
        ))}
      </div>
    </section>
  );
}
