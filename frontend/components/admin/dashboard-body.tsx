import { ThumbsDown, ThumbsUp } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { PERIOD_LABEL } from "@/lib/admin/filter";
import { count, ms, shortDate, withUnit } from "@/lib/admin/format";
import type { AdminDashboard } from "@/lib/admin/schemas";

import { AreaChart } from "./area-chart";
import { BarChart } from "./bar-chart";
import { DataTable } from "./data-table";
import { DonutChart } from "./donut-chart";
import { Heatmap } from "./heatmap";
import { MetricTile } from "./metric-tile";
import { Panel } from "./panel";
import { ReasonBars } from "./reason-bars";
import { StatCard } from "./stat-card";
import { TableUsage } from "./table-usage";

// Every card of the metrics dashboard below the filter row (mockup Admin), drawn from one AdminDashboard
export function DashboardBody({ d }: { d: AdminDashboard }) {
  const reasons = [
    { title: "👍 선택 이유 분포", rows: d.upReasons, icon: ThumbsUp },
    { title: "👎 선택 이유 분포", rows: d.downReasons, icon: ThumbsDown },
  ];
  const rates = [
    { label: "출처 클릭률", value: withUnit(d.rates.citationClick, "%"), note: "숫자 답 중 원표 클릭 비율" },
    { label: "대화당 평균 턴 수", value: `${d.rates.turnsPerChat.toFixed(1)}회`, note: `전주 ${d.rates.turnsPerChatPrev.toFixed(1)}회` },
    { label: "답 전 이탈률", value: withUnit(d.rates.dropBeforeAnswer, "%"), note: "답 생성 전 이탈" },
  ];
  return (
    <>
      <Card className="grid grid-cols-4 divide-x divide-line">
        {d.stats.map((s) => <StatCard key={s.key} stat={s} />)}
      </Card>

      <div className="grid grid-cols-[1.4fr_1fr] items-start gap-6">
        <Panel title="답 유형별 분포" sub={`${PERIOD_LABEL[d.filter.period]} 처리된 질문 기준`}>
          <div className="mt-6"><BarChart highlight={0} height={200} data={d.answerTypes} /></div>
        </Panel>
        <Panel title="거절 사유 분포">
          <div className="mt-4"><DonutChart segments={d.refusals} sub="총 거절" /></div>
          <div className="mt-5">
            <DataTable right={[2]} cols={["사유 코드", "설명", "건수"]} rows={d.refusals.map((r) => [r.code, r.label, count(r.count)])} />
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-2 gap-6">
        {reasons.map((r) => (
          <Panel key={r.title} title={r.title}><ReasonBars rows={r.rows} /></Panel>
        ))}
      </div>

      <div className="grid grid-cols-[1.4fr_1fr] items-start gap-6">
        <Panel title="일별 이용 추이" sub={`최근 ${d.daily.length}일 질문 수`}>
          <div className="mt-6">
            <AreaChart data={d.daily.map((p) => p.count)} labels={d.daily.map((p) => shortDate(p.day))} height={180} />
          </div>
        </Panel>
        <Panel title="시간대별 질문량">
          <div className="mt-5"><Heatmap levels={d.hourly} /></div>
        </Panel>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {rates.map((r) => (
          <Card key={r.label} className="p-5">
            <p className="text-[13px] text-sub">{r.label}</p>
            <p className="mt-1 text-2xl font-semibold">{r.value}</p>
            <p className="mt-1 text-xs text-mute">{r.note}</p>
          </Card>
        ))}
      </div>

      <TableUsage tables={d.tables} />

      <div className="grid grid-cols-2 gap-6">
        <Panel title="단계별 지연 p50·p95" sub="목표: p95 30초 이내">
          <div className="mt-4">
            <DataTable right={[1, 2]} cols={["노드·툴", "p50(ms)", "p95(ms)"]} rows={d.latency.map((l) => [l.stage, count(l.p50), count(l.p95)])} />
          </div>
        </Panel>
        <Panel title="첫 글자까지 시간(TTFT)" sub="목표: p95 30초 이내">
          <div className="mt-4"><AreaChart data={d.ttft.seconds} height={110} /></div>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <MetricTile tight label="p50" value={ms(d.ttft.p50)} />
            <MetricTile tight label="p95" value={ms(d.ttft.p95)} />
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-[1.4fr_1fr] items-start gap-6">
        <Panel title="질문당 LLM 사용량">
          <div className="mt-4 grid grid-cols-4 gap-3">
            <MetricTile label="평균 호출 수" value={`${d.llm.calls.toFixed(1)}회`} />
            <MetricTile label="평균 입력 토큰" value={count(d.llm.inputTokens)} />
            <MetricTile label="평균 출력 토큰" value={count(d.llm.outputTokens)} />
            <MetricTile label="평균 비용(USD)" value={`$${d.llm.costUsd.toFixed(3)}`} />
          </div>
        </Panel>
        <Panel title="로그 품질">
          <div className="mt-4">
            <DataTable
              cols={["항목", "값", "상태"]}
              rows={d.logQuality.map((q) => [
                q.label,
                withUnit(q.value, q.unit),
                <Badge key="s" variant={q.ok ? "green" : "red"}>{q.ok ? "정상" : "확인 필요"}</Badge>,
              ])}
            />
          </div>
        </Panel>
      </div>
    </>
  );
}
