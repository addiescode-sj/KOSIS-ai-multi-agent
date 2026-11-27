"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { cn } from "cn";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cut, kstTime } from "@/lib/admin/format";
import { questionsQuery } from "@/lib/admin/queries";
import type { QuestionList } from "@/lib/admin/screens";

import { AdminNotice } from "./admin-notice";
import { AdminShell } from "./admin-shell";
import { useLabel, useOperator } from "./operator-context";
import { Pager } from "./pager";
import { ReadFailure } from "./read-failure";

const FIELD = "h-10 rounded-lg border border-line bg-white px-3 text-sm outline-none focus:border-brand-500 focus:ring-4 focus:ring-brand-100";

// `/admin/questions`: every question of the period, newest first (today included; 90 days back at most). The filter
// lives in the address and the operator app reads it as it is, so a reload or a shared link shows the same list.
export function QuestionsScreen({ query }: { query: string }) {
  const params = new URLSearchParams(query);
  const { data, isPending } = useQuery(questionsQuery(query));
  return (
    <AdminShell title="질문 검수" sub="질문마다 결과·실패 단계·고른 표·피드백 · 답 문장은 저장하지 않습니다">
      <div className={cn("space-y-4", isPending && "opacity-60")} aria-busy={isPending}>
        {data?.ok && <Filter list={data.data} />}
        {data?.ok && data.data.notice && <p role="status" className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">{data.data.notice}</p>}
        {isPending ? <p className="text-sm text-mute">불러오는 중…</p> : data && (data.ok ? <Rows list={data.data} params={params} /> : <ReadFailure {...data} />)}
      </div>
    </AdminShell>
  );
}

function Filter({ list }: { list: QuestionList }) {
  const { outcomes } = useOperator();
  const label = useLabel();
  const f = list.filter;
  return (
    <form method="get" className="flex flex-wrap items-end gap-3" key={JSON.stringify(f)}>
      <label className="text-[13px] text-sub">시작<input type="date" name="start" defaultValue={f.start} className={cn(FIELD, "mt-1 block")} /></label>
      <label className="text-[13px] text-sub">끝<input type="date" name="end" defaultValue={f.end} className={cn(FIELD, "mt-1 block")} /></label>
      <label className="text-[13px] text-sub">결과
        <select name="outcome" defaultValue={f.outcome ?? ""} className={cn(FIELD, "mt-1 block w-32")}>
          <option value="">전체</option>
          {outcomes.map((o) => <option key={o} value={o}>{label("outcome", o)}</option>)}
        </select>
      </label>
      <label className="text-[13px] text-sub">사용자
        <select name="user" defaultValue={f.user} className={cn(FIELD, "mt-1 block w-28")}>
          {["all", "member", "guest"].map((u) => <option key={u} value={u}>{label("user", u)}</option>)}
        </select>
      </label>
      <label className="flex h-10 items-center gap-2 text-sm text-ink">
        <input type="checkbox" name="internal" value="true" defaultChecked={f.internal} className="size-4" /> 팀·평가 요청 포함
      </label>
      <Button type="submit" size="default">보기</Button>
    </form>
  );
}

function Rows({ list, params }: { list: QuestionList; params: URLSearchParams }) {
  const label = useLabel();
  if (list.rows.length === 0) return <AdminNotice title="이 기간에 질문이 없습니다" text="기간이나 결과를 바꿔 보세요." />;
  return (
    <Card className="p-6">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-mute">
            {["시각", "질문", "결과", "실패 단계", "표", "피드백"].map((c) => <th key={c} className="pb-2 font-medium">{c}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {list.rows.map((t) => (
            <tr key={t.request_id}>
              <td className="whitespace-nowrap py-2.5 text-sub">{kstTime(t.created_at)}</td>
              <td className="py-2.5"><Link href={`/admin/questions/${t.request_id}`} className="text-ink hover:text-brand-600 hover:underline">{cut(t.question, 40)}</Link></td>
              <td className="py-2.5 text-sub">{label("outcome", t.outcome)}{t.reason_code ? ` · ${label("reason", t.reason_code)}` : ""}</td>
              <td className="py-2.5 text-sub">{label("stage", t.failed_stage, "")}</td>
              <td className="py-2.5 text-sub" title={t.picked_key ?? undefined}>{t.survey_name ?? "–"}</td>
              <td className="py-2.5">{t.feedback === "up" ? "👍" : t.feedback === "down" ? "👎" : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Pager path="/admin/questions" params={params} page={list.page_no} size={list.page_size} total={list.total} />
    </Card>
  );
}
