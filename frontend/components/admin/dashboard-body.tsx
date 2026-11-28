import { Card } from "@/components/ui/card";
import { count, ms, shortDate, withUnit } from "@/lib/admin/format";
import type { DashboardView } from "@/lib/admin/view";

import { AreaChart } from "./area-chart";
import { BarChart } from "./bar-chart";
import { DataTable } from "./data-table";
import { DonutChart } from "./donut-chart";
import { Heatmap } from "./heatmap";
import { MetricTile } from "./metric-tile";
import { NoData } from "./no-data";
import { Panel } from "./panel";
import { ReasonBars } from "./reason-bars";
import { StatCard } from "./stat-card";
import { TableUsage } from "./table-usage";

const num = (v: number | null, digits: number, unit = "") => (v == null ? "–" : `${v.toFixed(digits)}${unit}`);
const tokens = (v: number | null) => (v == null ? "–" : count(Math.round(v)));

// Every card of the metrics dashboard below the filter row (mockup layout, T3-65), drawn from the operator app's
// numbers (T3-66). Cards with no stored source say "데이터 없음" and why.
export function DashboardBody({ d }: { d: DashboardView }) {
  const reasons = [
    { title: "👍 선택 이유 분포", rows: d.upReasons },
    { title: "👎 선택 이유 분포", rows: d.downReasons },
  ];
  const rates = [
    { label: "출처 클릭률", ...d.rates.citationClick },
    { label: "여러 번 물은 대화", ...d.rates.multiTurn },
    { label: "답 전 이탈률", ...d.rates.dropBeforeAnswer },
    { label: "답변 복사율", ...d.rates.copyAnswer },
    { label: "재시도율", ...d.rates.retry },
  ];
  return (
    <>
      <Card className="grid grid-cols-4 divide-x divide-line">
        {d.stats.map((s) => <StatCard key={s.key} stat={s} />)}
      </Card>

      <div className="grid grid-cols-[1.4fr_1fr] items-start gap-6">
        <Panel title="답 결과별 분포" sub="답함·거절·되묻기·오류 (답 전 이탈 제외)">
          {d.answerTypes.length === 0 ? (
            <NoData why="이 기간에 질문이 없습니다." />
          ) : (
            <div className="mt-6"><BarChart highlight={0} height={200} data={d.answerTypes} /></div>
          )}
        </Panel>
        <Panel title="거절 사유 분포">
          {d.refusals.length === 0 ? (
            <NoData why="이 기간에 거절한 질문이 없습니다." />
          ) : (
            <>
              <div className="mt-4"><DonutChart segments={d.refusals} sub="총 거절" /></div>
              <div className="mt-5">
                <DataTable right={[2]} cols={["사유 코드", "설명", "건수"]} rows={d.refusals.map((r) => [r.code, r.label, count(r.count)])} />
              </div>
            </>
          )}
        </Panel>
      </div>

      <div className="grid grid-cols-2 gap-6">
        {reasons.map((r) => (
          <Panel key={r.title} title={r.title}>
            {r.rows.length ? <ReasonBars rows={r.rows} /> : <NoData why="이 기간에 받은 피드백이 없습니다." />}
          </Panel>
        ))}
      </div>

      <div className="grid grid-cols-[1.4fr_1fr] items-start gap-6">
        <Panel title="일별 이용 추이" sub={`${d.daily.length}일 질문 수`}>
          {d.daily.length > 1 ? (
            <div className="mt-6">
              <AreaChart data={d.daily.map((p) => p.count)} labels={d.daily.map((p) => shortDate(p.day))} height={180} />
            </div>
          ) : (
            <NoData why={d.daily.length ? `${d.daily[0].day} 하루 ${count(d.daily[0].count)}건 (선은 2일 이상에서 그림)` : "기간이 없습니다."} />
          )}
        </Panel>
        <Panel title="시간대별 질문량">
          {d.hourly ? <div className="mt-5"><Heatmap levels={d.hourly} /></div> : <NoData why="요일·시간대 집계는 아직 저장하지 않습니다." />}
        </Panel>
      </div>

      <div className="grid grid-cols-5 gap-6">
        {rates.map((r) => (
          <Card key={r.label} className="p-5">
            <p className="text-[13px] text-sub">{r.label}</p>
            <p className={r.value == null ? "mt-1 text-2xl font-semibold text-mute" : "mt-1 text-2xl font-semibold"}>
              {r.value == null ? "데이터 없음" : withUnit(r.value, "%")}
            </p>
            <p className="mt-1 text-xs text-mute">{r.note}</p>
          </Card>
        ))}
      </div>

      <TableUsage tables={d.tables} />

      <div className="grid grid-cols-2 gap-6">
        <Panel title="응답 시간 p50·p95" sub="질문 → 답 끝, 끝까지 답한 질문만 · 목표: p95 30초 이내">
          {d.latency && (d.latency.p50 != null || d.latency.p95 != null) ? (
            <div className="mt-4 grid grid-cols-2 gap-3">
              <MetricTile tight label="p50" value={d.latency.p50 == null ? "–" : ms(Math.round(d.latency.p50))} />
              <MetricTile tight label="p95" value={d.latency.p95 == null ? "–" : ms(Math.round(d.latency.p95))} />
            </div>
          ) : (
            <NoData why="이 기간에 끝난 답이 없습니다." />
          )}
          <p className="mt-3 text-xs text-mute">단계별(노드·툴) 지연은 Prometheus 지표라 여기 없습니다(Grafana에서 확인).</p>
        </Panel>
        <Panel title="첫 글자까지 시간(TTFT)" sub="목표: p95 30초 이내">
          {d.ttft && (d.ttft.p50 != null || d.ttft.p95 != null) ? (
            <div className="mt-4 grid grid-cols-2 gap-3">
              <MetricTile tight label="p50" value={d.ttft.p50 == null ? "–" : ms(Math.round(d.ttft.p50))} />
              <MetricTile tight label="p95" value={d.ttft.p95 == null ? "–" : ms(Math.round(d.ttft.p95))} />
            </div>
          ) : (
            <NoData why="이 기간에 끝난 답이 없습니다." />
          )}
        </Panel>
      </div>

      <div className="grid grid-cols-[1.4fr_1fr] items-start gap-6">
        <Panel title="질문당 LLM 사용량" sub="질문 1건 평균">
          {d.llm ? (
            <div className="mt-4 grid grid-cols-4 gap-3">
              <MetricTile label="평균 호출 수" value={num(d.llm.calls, 1, "회")} />
              <MetricTile label="평균 입력 토큰" value={tokens(d.llm.inputTokens)} />
              <MetricTile label="평균 출력 토큰" value={tokens(d.llm.outputTokens)} />
              <MetricTile label="평균 비용(USD)" value="데이터 없음" />
            </div>
          ) : (
            <NoData why="이 기간에 질문이 없습니다." />
          )}
        </Panel>
        <Panel title="로그 품질" sub="수신 검사에서 떨어진 화면 이벤트 · 회원·내부 필터 무관">
          {d.logQuality.length ? (
            <div className="mt-4">
              <DataTable right={[2]} cols={["사유", "이벤트", "건수"]} rows={d.logQuality.map((r) => [r.reason, r.eventType, count(r.count)])} />
            </div>
          ) : (
            <NoData why="이 기간에 떨어진 화면 이벤트가 없습니다." />
          )}
          <p className="mt-3 text-xs text-mute">서버 로그 쓰기 실패 수는 백엔드 /ops/log-counts에 있습니다(DB 지표 아님).</p>
        </Panel>
      </div>
    </>
  );
}
