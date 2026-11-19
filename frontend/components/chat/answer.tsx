"use client";

import {
  AlertTriangle, Ban, CalendarX2, Check, CircleCheck, CircleSlash, ExternalLink, HelpCircle, Info, ListX, Loader2,
  Scale, SearchX, Sigma, type LucideIcon,
} from "lucide-react";
import dynamic from "next/dynamic";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "cn";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  answerKind, citation, headline, NODE_LABELS, progress, refusalReason, refusalTitle, STEPS, type Tone,
} from "@/lib/answer";
import type { ChatMessage } from "@/lib/chat-types";
import type { ChatResult } from "@/lib/schemas";

// Recharts loads only when an answer has a chart (the home page and refusals do not need it)
const StatChart = dynamic(() => import("./stat-chart").then((m) => m.StatChart), {
  ssr: false,
  loading: () => <div className="h-[220px] animate-pulse rounded-xl bg-canvas" />,
});

export const EXAMPLES = ["2023년 합계출산율은?", "2024년 출생아 수와 사망자 수 알려 줘", "최근 5년 혼인 건수 추이", "서울 고령 인구 비율"];

const REASON_ICONS: Record<string, LucideIcon> = {
  no_period: CalendarX2, no_item: SearchX, class_mismatch: ListX, unit_mismatch: Scale,
  not_aggregatable: Sigma, causal: Ban, missing_value: CircleSlash, out_of_scope: SearchX,
};

// One assistant turn: progress while the stream runs, then the card for the turn's outcome.
export function AssistantMessage({ message, running, onAsk }: {
  message: ChatMessage;
  running: boolean;
  onAsk: (text: string) => void;
}) {
  let result: ChatResult | undefined;
  let draft = "";
  const notices: string[] = [];
  for (const p of message.parts) {
    if (p.type === "data-result") result = p.data;
    else if (p.type === "data-draft") draft = p.data.text;
    else if (p.type === "data-notice") notices.push(p.data.text);
  }
  return (
    <div className="flex flex-col gap-3">
      {notices.map((n) => (
        <p key={n} className="flex items-center gap-2 rounded-xl bg-amber-50 px-4 py-2.5 text-[13px] text-amber-800">
          <Info size={15} />
          {n}
        </p>
      ))}
      {result ? <ResultCard result={result} onAsk={onAsk} /> : <Generating message={message} draft={draft} running={running} />}
    </div>
  );
}

function Generating({ message, draft, running }: { message: ChatMessage; draft: string; running: boolean }) {
  const done = progress(message);
  const nodes = message.parts.flatMap((p) => (p.type === "data-node" ? [p.data] : []));
  return (
    <Card className="space-y-5 p-6">
      <div className="flex items-center gap-3">
        {running ? <Loader2 size={20} className="animate-spin text-brand-600" /> : <AlertTriangle size={20} className="text-mute" />}
        <div>
          <h2 className="font-semibold">{running ? "답변을 생성하고 있습니다" : "답변이 중단되었습니다"}</h2>
          <p className="text-[13px] text-sub">통계표를 검색하고 조건을 확인한 뒤, 코드가 수치를 계산합니다.</p>
        </div>
      </div>
      <Steps done={done} />
      {nodes.length > 0 && (
        <ol className="space-y-1 text-[13px] text-sub">
          {nodes.map((n) => (
            <li key={n.name} className="flex items-center gap-2">
              <Check size={14} className="text-brand-600" />
              {NODE_LABELS[n.name] ?? n.name}
              <span className="tabular-nums text-mute">{(n.seconds * 1000).toFixed(0)} ms</span>
            </li>
          ))}
        </ol>
      )}
      {draft && <p className="text-[15px] leading-relaxed whitespace-pre-wrap text-sub">{draft}</p>}
    </Card>
  );
}

export function Steps({ done }: { done: number }) {
  return (
    <div className="space-y-2" aria-label={`${STEPS.length}단계 중 ${done}단계 완료`}>
      <div className="flex gap-1.5">
        {STEPS.map((s, i) => (
          <div key={s} className={cn("h-1.5 flex-1 rounded-full", i < done ? "bg-brand-600" : "bg-brand-100")} />
        ))}
      </div>
      <div className="flex justify-between text-xs text-mute">
        {STEPS.map((s, i) => (
          <span key={s} className={cn(i < done && "font-medium text-brand-700")}>{s}</span>
        ))}
      </div>
    </div>
  );
}

export function ResultCard({ result, onAsk }: { result: ChatResult; onAsk?: (text: string) => void }) {
  const kind = answerKind(result);
  const interpreted = result.resolved_question ?? result.question;
  return (
    <Card className="space-y-5 p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-mute">
          해석된 질문 · <span className="text-sub">{interpreted}</span>
        </p>
        <div className="flex gap-1.5">
          {result.reused_table && <Badge variant="blue">직전 표 이어서</Badge>}
          {result.degraded && <Badge variant="amber">간이 검색 순서</Badge>}
          {kind === "answered" && <Badge variant="green">계산 완료</Badge>}
          {kind === "partial" && <Badge variant="amber">일부만 답변</Badge>}
        </div>
      </div>
      {kind === "answered" && <Answered result={result} />}
      {kind === "partial" && <Partial result={result} />}
      {kind === "clarify" && <Clarify result={result} onAsk={onAsk} />}
      {kind === "refused" && <Refused result={result} onAsk={onAsk} />}
      {kind === "failed" && (
        <Notice icon={AlertTriangle} tone="red" title="답을 만들지 못했습니다">
          <p className="whitespace-pre-wrap">{result.answer || result.chart_note}</p>
        </Notice>
      )}
      {result.chart_note && kind !== "failed" && <p className="text-[13px] text-amber-700">{result.chart_note}</p>}
      <SourceBlock result={result} />
    </Card>
  );
}

function Answered({ result }: { result: ChatResult }) {
  const head = result.sub_results ? null : headline(result); // a multi-question turn has no single headline
  const caveats = result.stat_answer?.caveats ?? [];
  return (
    <>
      <Steps done={STEPS.length} />
      {(head || result.chart) && (
        <div className={cn("grid gap-6 border-t border-line pt-6", head && result.chart && "md:grid-cols-[220px_1fr]")}>
          {head && (
            <div>
              <Label>핵심 수치</Label>
              <p className="mt-1 text-[40px] leading-none font-bold tracking-tight">
                {head.value}
                {head.unit && <span className="ml-1 text-xl font-semibold text-sub">{head.unit}</span>}
              </p>
              {head.period && <p className="mt-3 text-xs text-mute">기준 기간: {head.period}</p>}
            </div>
          )}
          {result.chart && <StatChart spec={result.chart} />}
        </div>
      )}
      <div className="space-y-1 border-t border-line pt-5">
        <Label>답변</Label>
        <p className="text-[15px] leading-relaxed whitespace-pre-wrap">{result.answer}</p>
      </div>
      {caveats.length > 0 && (
        <ul className="space-y-1 rounded-xl bg-amber-50/70 px-4 py-3 text-[13px] text-amber-900">
          {caveats.map((c) => <li key={c.kind + c.text}>· {c.text}</li>)}
        </ul>
      )}
    </>
  );
}

function Partial({ result }: { result: ChatResult }) {
  const subs = result.sub_results ?? [];
  const ok = subs.filter((s) => s.outcome === "answered");
  const rest = subs.filter((s) => s.outcome !== "answered");
  return (
    <>
      <p className="text-xs text-mute">{subs.length}개 질문 중 {ok.length}개 답변 완료</p>
      <div className="space-y-3 border-t border-line pt-5">
        <p className="flex items-center gap-2 text-sm font-semibold text-emerald-700"><CircleCheck size={16} />답변 가능한 내용</p>
        {ok.map((s) => <p key={s.question} className="text-[15px] leading-relaxed whitespace-pre-wrap">{s.text}</p>)}
        {result.chart && <StatChart spec={result.chart} />}
      </div>
      <div className="space-y-1.5 rounded-xl border border-dashed border-amber-300 bg-amber-50/60 p-4">
        <p className="flex items-center gap-2 text-sm font-semibold text-amber-800"><CircleSlash size={16} />답변하지 못한 내용</p>
        {rest.map((s) => <p key={s.question} className="text-[13px] whitespace-pre-wrap text-amber-900">{s.text}</p>)}
      </div>
    </>
  );
}

function Clarify({ result, onAsk }: { result: ChatResult; onAsk?: (text: string) => void }) {
  const options = result.stat_answer?.clarify_options ?? [];
  return (
    <>
      <Notice icon={HelpCircle} tone="brand" title={result.answer}>
        <p>질문하신 내용을 정확히 답하려면 조건을 하나 더 확인해야 합니다.</p>
      </Notice>
      {options.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {options.map((o) => <Chip key={o} onClick={onAsk && (() => onAsk(o))}>{o}</Chip>)}
        </div>
      )}
      <p className="text-xs text-mute">선택지에 없으면 아래 입력창에 조건을 직접 적어 주세요.</p>
    </>
  );
}

function Refused({ result, onAsk }: { result: ChatResult; onAsk?: (text: string) => void }) {
  const reason = refusalReason(result);
  const { title, tone } = refusalTitle(reason);
  const available = result.stat_answer?.refusal?.available;
  const suggest = reason === "causal" || reason === "out_of_scope" || result.route === "refuse";
  return (
    <>
      <Notice icon={(reason && REASON_ICONS[reason]) || SearchX} tone={tone} title={title}>
        <p className="whitespace-pre-wrap">{result.answer}</p>
        {available && <p>대신 확인할 수 있는 범위: <b className="text-ink">{available}</b></p>}
      </Notice>
      {reason && <div className="flex gap-2"><Badge variant="gray">{reason}</Badge></div>}
      {suggest && onAsk && (
        <div className="rounded-xl bg-canvas p-4">
          <p className="text-[13px] font-medium">대신 이런 숫자 질문은 확인할 수 있어요</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {EXAMPLES.slice(0, 2).map((e) => <Chip key={e} onClick={() => onAsk(e)}>{e}</Chip>)}
          </div>
        </div>
      )}
    </>
  );
}

function SourceBlock({ result }: { result: ChatResult }) {
  const c = citation(result);
  if (!c) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-canvas px-4 py-3">
      <div className="min-w-0">
        <Label>출처</Label>
        <p className="mt-0.5 text-[13px] text-ink">{c.text}</p>
      </div>
      {c.url && (
        <a href={c.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[13px] font-medium text-brand-600 hover:underline">
          원표 보기 <ExternalLink size={13} />
        </a>
      )}
    </div>
  );
}

const TONES: Record<Tone | "brand", string> = {
  brand: "bg-brand-50 text-brand-600",
  amber: "bg-amber-50 text-amber-600",
  red: "bg-red-50 text-red-500",
  gray: "bg-gray-100 text-sub",
};

function Notice({ icon: Icon, tone, title, children }: { icon: LucideIcon; tone: Tone | "brand"; title: string; children: ReactNode }) {
  return (
    <div className="flex gap-4">
      <span className={cn("flex size-10 shrink-0 items-center justify-center rounded-full", TONES[tone])}><Icon size={19} /></span>
      <div className="min-w-0 flex-1">
        <h2 className="text-[17px] font-semibold">{title}</h2>
        <div className="mt-1.5 space-y-1 text-sm text-sub">{children}</div>
      </div>
    </div>
  );
}

export function Label({ children }: { children: ReactNode }) {
  return <span className="text-xs font-medium text-mute">{children}</span>;
}

export function Chip({ active, className, ...props }: ComponentProps<"button"> & { active?: boolean }) {
  return (
    <button
      type="button"
      className={cn(
        "h-8 cursor-pointer rounded-full border px-3.5 text-[13px] font-medium transition-colors disabled:cursor-default",
        active ? "border-brand-600 bg-brand-600 text-white" : "border-line bg-white text-sub hover:border-brand-300 hover:text-brand-600",
        className,
      )}
      {...props}
    />
  );
}
