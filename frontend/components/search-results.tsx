"use client";

import { ArrowRight, ExternalLink, Loader2, MessageSquare, Search as SearchIcon, SearchX, Table2, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Tabs } from "radix-ui";
import { useEffect, useState, useSyncExternalStore } from "react";

import { AppShell } from "@/components/app-shell";
import { Chip } from "@/components/chat/answer";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { textOf } from "@/lib/chat-types";
import { type Conversation, conversations, matchConversations } from "@/lib/conversations";
import { type SearchOutput, type TableCandidate } from "@/lib/schemas";

const EMPTY: Conversation[] = [];
type Tables = { state: "idle" | "loading" } | { state: "done"; out: SearchOutput } | { state: "error"; message: string };

// `/search?q=`: this browser's conversations that mention the words, and the KOSIS tables phase 2 search finds.
export function SearchResults() {
  const router = useRouter();
  const q = (useSearchParams().get("q") ?? "").trim();
  const list = useSyncExternalStore(conversations.subscribe, conversations.list, () => EMPTY);
  const convs = q ? matchConversations(list, q) : [];
  const tables = useTables(q);
  const tableList = tables.state === "done" ? tables.out.picked.concat(tables.out.candidates.filter((c) => !tables.out.picked.some((p) => p.key === c.key))) : [];
  const total = convs.length + tableList.length;
  const submit = (v: string) => router.push(v ? `/search?q=${encodeURIComponent(v)}` : "/search");

  return (
    <AppShell title="검색 결과" sub={q ? `‘${q}’ 검색 결과 ${total}건` : "대화와 통계표를 검색합니다"} query={q}>
      <div className="mx-auto max-w-5xl space-y-6">
        <form
          role="search"
          onSubmit={(e) => { e.preventDefault(); submit(new FormData(e.currentTarget).get("q")?.toString().trim() ?? ""); }}
          className="flex items-center gap-3 rounded-2xl border border-line bg-white px-4 py-2 shadow-[0_8px_30px_rgba(17,24,39,0.05)] focus-within:border-brand-500"
        >
          <SearchIcon size={20} className="text-mute" />
          <input name="q" key={q} defaultValue={q} aria-label="검색어" className="h-11 flex-1 text-[16px] outline-none" placeholder="대화나 통계표를 검색하세요" />
          {q && <button type="button" onClick={() => submit("")} className="cursor-pointer rounded-md p-1 text-mute hover:bg-gray-100" aria-label="지우기"><X size={16} /></button>}
          <Button type="submit">검색</Button>
        </form>

        {!q ? null : tables.state !== "loading" && total === 0 ? (
          <Card className="flex flex-col items-center px-8 py-16 text-center">
            <span className="flex size-14 items-center justify-center rounded-full bg-canvas text-mute"><SearchX size={26} /></span>
            <h2 className="mt-5 text-lg font-semibold">‘{q}’에 대한 결과가 없어요</h2>
            <p className="mt-1.5 text-sm text-sub">
              {tables.state === "error" ? tables.message : tables.state === "done" && tables.out.refusal_text ? tables.out.refusal_text : "검색어를 바꿔 보거나, 궁금한 내용을 바로 질문해 보세요."}
            </p>
            <Button asChild className="mt-6"><a href={`/?q=${encodeURIComponent(q)}`}>‘{q}’ 바로 질문하기</a></Button>
          </Card>
        ) : (
          <Tabs.Root defaultValue="all" className="space-y-6">
            <Tabs.List className="flex gap-2" aria-label="검색 결과 종류">
              {([["all", "전체", total], ["convs", "내 대화", convs.length], ["tables", "통계표", tableList.length]] as const).map(([v, label, n]) => (
                <Tabs.Trigger key={v} value={v} asChild>
                  <Chip className="data-[state=active]:border-brand-600 data-[state=active]:bg-brand-600 data-[state=active]:text-white">
                    {label} <span className="opacity-70">{n}</span>
                  </Chip>
                </Tabs.Trigger>
              ))}
            </Tabs.List>
            <Tabs.Content value="all" className="grid items-start gap-6 lg:grid-cols-[1fr_340px]">
              <ConvSection convs={convs} q={q} />
              <TableSection tables={tables} list={tableList} q={q} />
            </Tabs.Content>
            <Tabs.Content value="convs"><ConvSection convs={convs} q={q} /></Tabs.Content>
            <Tabs.Content value="tables"><TableSection tables={tables} list={tableList} q={q} wide /></Tabs.Content>
          </Tabs.Root>
        )}
      </div>
    </AppShell>
  );
}

function useTables(q: string): Tables {
  // The last finished search and the query it was for; a newer query shows as loading until its answer comes
  const [last, setLast] = useState<{ q: string; tables: Tables } | null>(null);
  useEffect(() => {
    if (!q) return;
    const ctrl = new AbortController();
    fetch("/api/tables", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ question: q }), signal: ctrl.signal })
      .then(async (res) => {
        const body = await res.json();
        setLast({ q, tables: res.ok ? { state: "done", out: body } : { state: "error", message: body.error ?? "통계표 검색에 실패했습니다." } });
      })
      .catch((e) => !ctrl.signal.aborted && setLast({ q, tables: { state: "error", message: `통계표 검색에 실패했습니다 (${e})` } }));
    return () => ctrl.abort();
  }, [q]);
  if (!q) return { state: "idle" };
  return last?.q === q ? last.tables : { state: "loading" };
}

function ConvSection({ convs, q }: { convs: Conversation[]; q: string }) {
  return (
    <section>
      <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-sub"><MessageSquare size={16} />내 대화 <span className="text-mute">{convs.length}</span></h2>
      {convs.length ? (
        <Card className="divide-y divide-line">
          {convs.map((c) => {
            const question = c.messages.find((m) => m.role === "user");
            const answer = c.messages.findLast((m) => m.role === "assistant");
            return (
              <Link key={c.id} href={`/c/${c.id}`} className="group block px-5 py-4 hover:bg-gray-50/70">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-[15px] font-semibold"><Hl text={c.title} q={q} /></p>
                  <span className="shrink-0 text-xs text-mute">{new Date(c.updatedAt).toLocaleDateString("ko-KR")}</span>
                </div>
                {question && <p className="mt-2 text-[13px] text-sub"><span className="mr-1.5 font-semibold text-ink">Q</span><Hl text={textOf(question)} q={q} /></p>}
                {answer && <p className="mt-1 line-clamp-2 text-[13px] leading-relaxed text-sub"><span className="mr-1.5 font-semibold text-brand-600">A</span><Hl text={textOf(answer)} q={q} /></p>}
              </Link>
            );
          })}
        </Card>
      ) : (
        <Card className="px-5 py-8 text-center text-sm text-sub">일치하는 대화가 없어요.</Card>
      )}
    </section>
  );
}

function TableSection({ tables, list, q, wide }: { tables: Tables; list: TableCandidate[]; q: string; wide?: boolean }) {
  return (
    <section>
      <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-sub"><Table2 size={16} />KOSIS 통계표 <span className="text-mute">{list.length}</span></h2>
      {tables.state === "loading" && <Card className="flex items-center gap-2 px-5 py-8 text-sm text-sub"><Loader2 size={16} className="animate-spin text-brand-600" />통계표를 찾고 있습니다…</Card>}
      {tables.state === "error" && <Card className="px-5 py-8 text-center text-sm text-red-600">{tables.message}</Card>}
      {tables.state === "done" && !list.length && <Card className="px-5 py-8 text-center text-sm text-sub">{tables.out.refusal_text ?? "맞는 통계표가 없어요."}</Card>}
      <div className={wide ? "grid gap-3 md:grid-cols-2" : "grid gap-3"}>
        {list.map((t) => (
          <Card key={t.key} className="p-4">
            <div className="flex items-start justify-between gap-3">
              <p className="text-[15px] leading-snug font-semibold"><Hl text={t.table_name} q={q} /></p>
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

function Hl({ text, q }: { text: string; q: string }) {
  const i = q ? text.toLowerCase().indexOf(q.toLowerCase()) : -1;
  if (i < 0) return text;
  return <>{text.slice(0, i)}<mark className="rounded bg-brand-100 px-0.5 text-brand-700">{text.slice(i, i + q.length)}</mark>{text.slice(i + q.length)}</>;
}
