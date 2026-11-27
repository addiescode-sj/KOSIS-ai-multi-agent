"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { cn } from "cn";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cut, kstTime } from "@/lib/admin/format";
import { inboxQuery } from "@/lib/admin/queries";
import type { Inbox } from "@/lib/admin/screens";

import { AdminNotice } from "./admin-notice";
import { AdminShell } from "./admin-shell";
import { useLabel, useOperator } from "./operator-context";
import { Pager } from "./pager";
import { ReadFailure } from "./read-failure";
import { ReviewForm } from "./review-form";

const FIELD = "h-10 rounded-lg border border-line bg-white px-3 text-sm outline-none focus:border-brand-500 focus:ring-4 focus:ring-brand-100";

// `/admin/feedback`: thumbs-down answers of the questions asked in the period (30 days by default), newest feedback
// first, with their review state. Each row opens its review form in place.
export function FeedbackScreen({ query }: { query: string }) {
  const params = new URLSearchParams(query);
  const { data, isPending } = useQuery(inboxQuery(query));
  return (
    <AdminShell title="👎 처리함" sub="👎 받은 답을 확인하고 상태를 남깁니다 · 수정 요청은 Linear 번호와 함께">
      <div className={cn("space-y-4", isPending && "opacity-60")} aria-busy={isPending}>
        {data?.ok && <Filter inbox={data.data} />}
        {data?.ok && data.data.notice && <p role="status" className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">{data.data.notice}</p>}
        {isPending ? <p className="text-sm text-mute">불러오는 중…</p> : data && (data.ok ? <Rows inbox={data.data} params={params} /> : <ReadFailure {...data} />)}
      </div>
    </AdminShell>
  );
}

function Filter({ inbox }: { inbox: Inbox }) {
  const { statuses, labels } = useOperator();
  const label = useLabel();
  const f = inbox.filter;
  return (
    <form method="get" className="flex flex-wrap items-end gap-3" key={JSON.stringify(f)}>
      <label className="text-[13px] text-sub">상태
        <select name="status" defaultValue={f.status} className={cn(FIELD, "mt-1 block w-32")}>
          {statuses.map((s) => <option key={s} value={s}>{label("status", s)}</option>)}
          <option value="all">전체</option>
        </select>
      </label>
      <label className="text-[13px] text-sub">질문 시작일<input type="date" name="start" defaultValue={f.start} className={cn(FIELD, "mt-1 block")} /></label>
      <label className="text-[13px] text-sub">끝<input type="date" name="end" defaultValue={f.end} className={cn(FIELD, "mt-1 block")} /></label>
      <label className="text-[13px] text-sub">👎 이유
        <select name="category" defaultValue={f.category ?? ""} className={cn(FIELD, "mt-1 block w-40")}>
          <option value="">전체</option>
          {Object.entries(labels.down ?? {}).map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          <option value="none">선택 안 함</option>
        </select>
      </label>
      <label className="text-[13px] text-sub">담당
        <select name="assignee" defaultValue={f.assignee ?? ""} className={cn(FIELD, "mt-1 block w-32")}>
          <option value="">전체</option>
          <option value="me">나</option>
          {inbox.people.map((p) => <option key={p.id} value={p.id}>{p.name ?? p.id.slice(0, 8)}</option>)}
        </select>
      </label>
      <Button type="submit">보기</Button>
    </form>
  );
}

function Rows({ inbox, params }: { inbox: Inbox; params: URLSearchParams }) {
  const label = useLabel();
  if (inbox.rows.length === 0) return <AdminNotice title="해당하는 👎가 없습니다" text="상태를 '전체'로 바꾸거나 기간을 늘려 보세요." />;
  return (
    <Card className="p-6">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-mute">
            {["받은 시각", "질문", "👎 이유", "결과·표", "상태", "담당"].map((c) => <th key={c} className="pb-2 font-medium">{c}</th>)}
          </tr>
        </thead>
        {inbox.rows.map((r) => (
          <tbody key={r.request_id} className="border-b border-line">
            <tr>
              <td className="whitespace-nowrap pt-3 text-sub">{kstTime(r.received_at)}</td>
              <td className="pt-3"><Link href={`/admin/questions/${r.request_id}`} className="text-ink hover:text-brand-600 hover:underline">{cut(r.question, 40)}</Link></td>
              <td className="pt-3 text-sub">{label("down", r.choice, "선택 안 함")}{r.feedback_reason ? ` · ${cut(r.feedback_reason, 30, "")}` : ""}</td>
              <td className="pt-3 text-sub">{label("outcome", r.outcome)} · {r.survey_name ?? "–"}</td>
              <td className="pt-3 text-sub">{label("status", r.status)}{r.issue_key ? ` · ${r.issue_key}` : ""}</td>
              <td className="pt-3 text-sub">{r.assignee ?? "–"}</td>
            </tr>
            <tr>
              <td colSpan={6} className="pb-3 pt-1">
                <details>
                  <summary className="cursor-pointer text-[13px] text-brand-600">처리</summary>
                  <div className="mt-3">
                    <ReviewForm
                      requestId={r.request_id}
                      people={inbox.people}
                      review={r.review_updated_at ? {
                        status: r.status, assignee_id: r.assignee_id, assignee: r.assignee, note: r.note, issue_key: r.issue_key,
                        updated_at: r.review_updated_at, updated_by: null,
                      } : null}
                    />
                  </div>
                </details>
              </td>
            </tr>
          </tbody>
        ))}
      </table>
      <Pager path="/admin/feedback" params={params} page={inbox.page_no} size={inbox.page_size} total={inbox.total} />
    </Card>
  );
}
