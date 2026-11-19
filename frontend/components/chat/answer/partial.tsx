import { CircleCheck, CircleSlash } from "lucide-react";

import type { ChatResult } from "@/lib/chat/schemas";

import { LazyStatChart } from "./lazy-stat-chart";

// A multi-question turn where some sub-answers were refused or need a condition.
export function Partial({ result }: { result: ChatResult }) {
  const subs = result.sub_results ?? [];
  const ok = subs.filter((s) => s.outcome === "answered");
  const rest = subs.filter((s) => s.outcome !== "answered");
  return (
    <>
      <p className="text-xs text-mute">{subs.length}개 질문 중 {ok.length}개 답변 완료</p>
      <div className="space-y-3 border-t border-line pt-5">
        <p className="flex items-center gap-2 text-sm font-semibold text-emerald-700"><CircleCheck size={16} />답변 가능한 내용</p>
        {ok.map((s) => <p key={s.question} className="text-[15px] leading-relaxed whitespace-pre-wrap">{s.text}</p>)}
        {result.chart && <LazyStatChart spec={result.chart} />}
      </div>
      <div className="space-y-1.5 rounded-xl border border-dashed border-amber-300 bg-amber-50/60 p-4">
        <p className="flex items-center gap-2 text-sm font-semibold text-amber-800"><CircleSlash size={16} />답변하지 못한 내용</p>
        {rest.map((s) => <p key={s.question} className="text-[13px] whitespace-pre-wrap text-amber-900">{s.text}</p>)}
      </div>
    </>
  );
}
