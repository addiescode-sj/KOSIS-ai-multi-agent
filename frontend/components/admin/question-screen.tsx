"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { kstTime, sec, count } from "@/lib/admin/format";
import { questionQuery } from "@/lib/admin/queries";
import type { QuestionDetail, Review } from "@/lib/admin/screens";

import { AdminShell } from "./admin-shell";
import { useLabel, useOperator } from "./operator-context";
import { Panel } from "./panel";
import { ReadFailure } from "./read-failure";
import { ReviewForm } from "./review-form";

// `/admin/questions/[id]`: everything stored about one question (the answer text is not stored, 11/26), its
// feedback and, for a thumbs-down, the review form. The operator app records each opening, so it is read once.
export function QuestionScreen({ id }: { id: string }) {
  const { data, isPending } = useQuery(questionQuery(id));
  return (
    <AdminShell title="질문 상세" sub={`request_id ${id}`} actions={<Link href="/admin/questions" className="flex items-center gap-1 text-sm text-brand-600 hover:underline"><ArrowLeft size={15} />질문 목록</Link>}>
      {isPending ? <p className="text-sm text-mute">불러오는 중…</p> : data && (data.ok ? <Body d={data.data} /> : <ReadFailure {...data} />)}
    </AdminShell>
  );
}

function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="mt-4 grid grid-cols-[7rem_1fr] gap-x-4 gap-y-2 text-sm">
      {rows.map(([k, v]) => [<dt key={`${k}-t`} className="text-mute">{k}</dt>, <dd key={`${k}-d`} className="text-ink">{v}</dd>])}
    </dl>
  );
}

function Body({ d }: { d: QuestionDetail }) {
  const label = useLabel();
  const { grafana } = useOperator();
  const client = useQueryClient();
  const r = d.row;
  const saved = (review: Review) =>
    client.setQueryData(questionQuery(r.request_id).queryKey, (old) => (old?.ok ? { ...old, data: { ...old.data, review } } : old));
  return (
    <div className="grid grid-cols-3 items-start gap-6">
      <Panel title="질문" className="col-span-3" action={<span />}>
        <p className="mt-3 text-[17px] text-ink">{r.question ?? (r.is_internal ? "(평가·팀 요청)" : "(보관 기간 지남)")}</p>
        {d.rewrites.map((w, i) => <p key={w.seq} className="mt-1 text-sm text-sub">다시 쓴 질문 {i + 1}: {w.text ?? "(보관 기간 지남)"}</p>)}
        <p className="mt-3 text-xs text-mute">
          해석 {label("rewrite", r.rewrite_kind)} · 해석한 곳 {r.interpreted_by ?? "–"} · {r.is_member ? "회원" : "비회원"} · {kstTime(r.created_at, true)}
        </p>
        <p className="mt-2 text-xs text-mute">답 문장은 저장하지 않습니다(11/26 팀 결정).</p>
      </Panel>
      <Panel title="결과" action={<span />}>
        <Facts rows={[
          ["결과", label("outcome", r.outcome)],
          ["사유", label("reason", r.reason_code)],
          ["실패 단계", label("stage", r.failed_stage)],
          ["표", <>{r.survey_name ?? "–"} {r.picked_key && <code className="text-xs text-mute">{r.picked_key}</code>}</>],
          ["숫자·차트", `${r.has_numbers ? "숫자 있음" : "숫자 없음"} · ${r.has_chart ? "차트 있음" : "차트 없음"}`],
        ]} />
      </Panel>
      <Panel title="시간·비용" action={<span />}>
        <Facts rows={[
          ["응답 시간", sec(r.latency_ms)],
          ["첫 글자", sec(r.first_token_ms)],
          ["LLM 호출", count(r.llm_calls)],
          ["토큰", `입력 ${count(r.input_tokens)} · 출력 ${count(r.output_tokens)}`],
        ]} />
      </Panel>
      <Panel title="피드백" action={<span />}>
        {d.feedback ? (
          <Facts rows={[
            ["값", d.feedback.value === "down" ? "👎" : "👍"],
            ["이유", label(d.feedback.value, d.feedback.category, "선택 안 함")],
            ["적은 말", d.feedback.reason ?? "–"],
            ["받은 시각", kstTime(d.feedback.updated_at)],
          ]} />
        ) : <p className="mt-4 text-sm text-mute">피드백 없음</p>}
      </Panel>
      <Panel title="같은 대화" sub="이 질문 앞뒤 7일" className="col-span-2" action={<span />}>
        <ol className="mt-4 space-y-1.5 text-sm">
          {d.conversation.map((c) => (
            <li key={c.request_id} className="flex gap-2">
              <span className="w-6 shrink-0 text-right text-mute">{c.turn_index + 1}</span>
              {c.request_id === r.request_id
                ? <b className="text-ink">{c.question ?? "–"}</b>
                : <Link href={`/admin/questions/${c.request_id}`} className="text-ink hover:text-brand-600 hover:underline">{c.question ?? "–"}</Link>}
              <span className="text-mute">· {label("outcome", c.outcome)}</span>
            </li>
          ))}
        </ol>
      </Panel>
      <Panel title="연결" action={<span />}>
        <Facts rows={[["request_id", <code key="id" className="break-all text-xs">{r.request_id}</code>]]} />
        {grafana && <p className="mt-3 text-sm"><a href={`${grafana}/explore`} target="_blank" rel="noreferrer" className="text-brand-600 hover:underline">Grafana에서 로그 찾기 ↗</a></p>}
      </Panel>
      {d.feedback?.value === "down" && (
        <Panel title="👎 처리" className="col-span-3" sub={d.review ? `마지막 변경 ${d.review.updated_by ?? "–"} · ${kstTime(d.review.updated_at)}` : "아직 처리 기록 없음"} action={<span />}>
          <div className="mt-4"><ReviewForm requestId={r.request_id} review={d.review} people={d.people} onSaved={saved} /></div>
        </Panel>
      )}
    </div>
  );
}
