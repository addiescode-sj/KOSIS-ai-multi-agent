import { SearchX } from "lucide-react";
import { Tabs } from "radix-ui";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Chip } from "@/components/ui/chip";
import { mergeTables, type TablesResult } from "@/lib/search/tables";
import type { Conversation } from "@/types/conversation";

import { ConversationSection } from "./conversation-section";
import { TableSection } from "./table-section";

// Tabs and counts over both kinds of result. Pure: the same view is the Suspense fallback (tables undefined)
// and the loaded page, so streaming in the tables does not move the layout.
export function ResultsView({ q, convs, tables }: { q: string; convs: Conversation[]; tables: TablesResult | undefined }) {
  const tableCount = tables?.ok ? mergeTables(tables.out).length : 0;
  const total = convs.length + tableCount;

  if (tables && total === 0) {
    return (
      <Card className="flex flex-col items-center px-8 py-16 text-center">
        <span className="flex size-14 items-center justify-center rounded-full bg-canvas text-mute"><SearchX size={26} /></span>
        <h2 className="mt-5 text-lg font-semibold">‘{q}’에 대한 결과가 없어요</h2>
        <p className="mt-1.5 text-sm text-sub">
          {!tables.ok ? tables.message : tables.out.refusal_text ?? "검색어를 바꿔 보거나, 궁금한 내용을 바로 질문해 보세요."}
        </p>
        <Button asChild className="mt-6"><a href={`/?q=${encodeURIComponent(q)}`}>‘{q}’ 바로 질문하기</a></Button>
      </Card>
    );
  }

  return (
    <Tabs.Root defaultValue="all" className="space-y-6">
      <Tabs.List className="flex gap-2" aria-label="검색 결과 종류">
        {([["all", "전체", tables ? total : null], ["convs", "내 대화", convs.length], ["tables", "통계표", tables ? tableCount : null]] as const).map(([v, label, n]) => (
          <Tabs.Trigger key={v} value={v} asChild>
            <Chip className="data-[state=active]:border-brand-600 data-[state=active]:bg-brand-600 data-[state=active]:text-white">
              {label} <span className="opacity-70">{n ?? "…"}</span>
            </Chip>
          </Tabs.Trigger>
        ))}
      </Tabs.List>
      <Tabs.Content value="all" className="grid items-start gap-6 lg:grid-cols-[1fr_340px]">
        <ConversationSection convs={convs} q={q} />
        <TableSection tables={tables} q={q} />
      </Tabs.Content>
      <Tabs.Content value="convs"><ConversationSection convs={convs} q={q} /></Tabs.Content>
      <Tabs.Content value="tables"><TableSection tables={tables} q={q} wide /></Tabs.Content>
    </Tabs.Root>
  );
}
