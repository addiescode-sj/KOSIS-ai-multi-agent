"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { cn } from "cn";

import { Card } from "@/components/ui/card";
import { count, kstTime, sec } from "@/lib/admin/format";
import { homeQuery } from "@/lib/admin/queries";
import type { Home } from "@/lib/admin/screens";

import { AdminShell } from "./admin-shell";
import { DataTable } from "./data-table";
import { useLabel } from "./operator-context";
import { Panel } from "./panel";
import { ReadFailure } from "./read-failure";

// `/admin`: today at a glance (KST 00:00 until now) and what needs a look: the last error and drop-off questions of
// the week, the newest thumbs-down not handled yet. Read again every minute (as the T3-57 home refreshed).
export function HomeScreen() {
  const { data, isPending } = useQuery(homeQuery());
  return (
    <AdminShell title="운영 홈" sub="오늘 0시(KST)부터 지금까지 · 1분마다 다시 읽음">
      {isPending ? <p className="text-sm text-mute">불러오는 중…</p> : data && (data.ok ? <Body h={data.data} /> : <ReadFailure {...data} />)}
    </AdminShell>
  );
}

function Body({ h }: { h: Home }) {
  const label = useLabel();
  const health = [
    { name: "DB", state: h.db_ok ? "정상" : "연결 안 됨", tone: h.db_ok ? "up" : "down" },
    { name: "서비스", state: "확인 안 됨", tone: "unknown" },
    { name: "GPU", state: "확인 안 됨", tone: "unknown" },
    { name: "배포 판", state: "확인 안 됨", tone: "unknown" },
  ] as const;
  const today = [
    { name: "질문", value: count(h.questions) },
    { name: "오류·실패", value: count(h.errors), bad: h.errors > 0 },
    { name: "답 전 이탈", value: count(h.aborted) },
    { name: "응답 시간 p95", value: sec(h.p95) },
    { name: "👎 새 글", value: count(h.new_down), href: "/admin/feedback", bad: h.new_down > 0 },
  ];
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-4 gap-4">
        {health.map((c) => (
          <Card key={c.name} className="flex items-center gap-3 p-4" title={c.tone === "unknown" ? "Prometheus 설치 뒤 표시합니다" : undefined}>
            <span className={cn("size-2.5 rounded-full", c.tone === "up" ? "bg-emerald-500" : c.tone === "down" ? "bg-red-500" : "bg-gray-300")} />
            <span className="text-sm text-sub">{c.name}</span>
            <span className={cn("ml-auto text-sm font-semibold", c.tone === "unknown" ? "text-mute" : c.tone === "down" ? "text-red-600" : "text-ink")}>{c.state}</span>
          </Card>
        ))}
      </div>

      <Card className="grid grid-cols-5 divide-x divide-line">
        {today.map((t) => (
          <div key={t.name} className="p-5">
            <p className="text-[13px] text-sub">{t.name}</p>
            <p className={cn("mt-1 text-[28px] font-semibold tracking-tight", t.bad && "text-red-600")}>
              {t.href ? <Link href={t.href} className="hover:underline">{t.value}</Link> : t.value}
            </p>
          </div>
        ))}
      </Card>

      <div className="grid grid-cols-2 items-start gap-6">
        <Panel title="최근 오류·이탈 질문" sub="최근 7일, 새것부터 5개" action={<Link href="/admin/questions" className="text-[13px] text-brand-600 hover:underline">질문 검수 →</Link>}>
          {h.problems.length === 0 ? (
            <p className="mt-5 text-sm text-mute">최근 7일에 오류·이탈 질문이 없습니다.</p>
          ) : (
            <div className="mt-4">
              <DataTable
                cols={["시각", "결과", ""]}
                right={[2]}
                rows={h.problems.map((t) => [
                  kstTime(t.created_at),
                  `${label("outcome", t.outcome)}${t.failed_stage ? ` · ${label("stage", t.failed_stage)}` : ""}`,
                  <Link key="go" href={`/admin/questions/${t.request_id}`} className="text-brand-600 hover:underline">상세</Link>,
                ])}
              />
            </div>
          )}
        </Panel>
        <Panel title="👎 새 글" sub="아직 처리하지 않은 것, 새것부터 5개" action={<Link href="/admin/feedback" className="text-[13px] text-brand-600 hover:underline">👎 처리함 →</Link>}>
          {h.inbox.length === 0 ? (
            <p className="mt-5 text-sm text-mute">새 👎가 없습니다.</p>
          ) : (
            <div className="mt-4">
              <DataTable
                cols={["받은 시각", "이유", ""]}
                right={[2]}
                rows={h.inbox.map((r) => [
                  kstTime(r.received_at),
                  label("down", r.choice, "선택 안 함"),
                  <Link key="go" href={`/admin/questions/${r.request_id}`} className="text-brand-600 hover:underline">처리</Link>,
                ])}
              />
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
