"""차트 서비스: 실전 1의 ChartSpec을 Vega-Lite v5 spec으로 바꾼다 (T3-23, T3-27에서 chart_type 추가).

변환 규칙(막대·꺾은선·원형 등)은 실전 1 kosis_chat.chart.vega.to_vega가 갖고 있고, 여기서는 다시 짜지 않는다.
차트 종류 자동 선택(시간 변화 = 꺾은선, 항목 비교 = 막대 …)은 T3-02 범위다. 이 파일은 사용자가 종류를 말했을 때
그 말을 실전 1의 종류 이름으로 옮기기만 한다(phase1_kind).

두 가지 입구가 있다.
- to_vega_lite(spec): 워크플로 노드가 직접 부르는 함수. 사용자가 차트를 요청하지 않은 턴에는 실전 1이 답과 함께 준
  차트를 그대로 그린다. 이때 make_chart 툴을 부르면 하네스가 기대 툴 목록에 없는 호출(extra_call)로 감점한다.
- make_chart 툴: 사용자가 차트를 요청했을 때("그 표로 막대그래프", "그래프로도 보여줘") 노드가 부른다.
  인자 chart_type은 하네스 채점 필드(evals/schemas.ARG_FIELDS)다. T3-07 ReAct에서도 같은 툴을 쓴다.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from kosis_ai.schemas import ChartSpec
from kosis_chat.chart.vega import to_vega

# 직전 답에 차트 데이터가 없을 때의 답 (코드 고정 문장)
NO_CHART_TEXT = "이전 답에는 그릴 수 있는 차트 데이터가 없어요. 숫자를 먼저 물어봐 주세요."
CHART_NAMES = {"bar": "막대그래프", "line": "꺾은선 그래프", "pie": "원형 차트"}


def to_vega_lite(spec: ChartSpec | None) -> dict | None:
    """ChartSpec → Vega-Lite dict. 차트가 없거나 실전 1이 모르는 종류면 None (리팩토링 전 chat_result와 같은 규칙)."""
    return to_vega(spec) if spec is not None else None


def phase1_kind(requested: str, current: str) -> str:
    """사용자가 말한 종류(bar | line | pie) → 실전 1 ChartSpec.kind.

    실전 1 종류는 데이터 모양까지 담는다(kosis_ai.schemas.ChartKind): bar(항목별 막대) · timeline(기간 순 막대) ·
    line(기간 꺾은선) · multi_line(계열별 꺾은선) · grouped_bar(기간마다 여러 막대) · pie. 그래서 "막대로"라고 해도
    지금 차트가 기간 꺾은선이면 기간 순 막대(timeline)로, 계열별 꺾은선이면 묶음 막대(grouped_bar)로 옮긴다.
    """
    if requested == "bar":
        return {"line": "timeline", "multi_line": "grouped_bar"}.get(current, current if current in ("bar", "timeline", "grouped_bar") else "bar")
    if requested == "line":
        return {"timeline": "line", "grouped_bar": "multi_line"}.get(current, current if current in ("line", "multi_line") else "line")
    return "pie"


def draw(spec: ChartSpec | None, chart_type: str | None) -> dict:
    """차트 하나를 그린다. 결과: {"vega_lite", "kind"(실제로 그린 실전 1 종류), "note"(요청대로 못 그렸을 때 이유)}."""
    if spec is None:
        return {"vega_lite": None, "kind": None, "note": NO_CHART_TEXT}
    if chart_type is None:
        return {"vega_lite": to_vega_lite(spec), "kind": spec.kind, "note": None}
    kind = phase1_kind(chart_type, spec.kind)
    # vega_lite가 이미 채워져 있으면 to_vega가 그것을 그대로 쓰므로 비운다
    redrawn = to_vega(spec.model_copy(update={"kind": kind, "vega_lite": None}))
    if redrawn is None:  # 실전 1이 그 종류를 그리지 못함 → 원래 종류로 그리고 사실을 남긴다
        return {"vega_lite": to_vega_lite(spec), "kind": spec.kind,
                "note": f"{CHART_NAMES.get(chart_type, chart_type)}로는 그릴 수 없어 원래 종류({spec.kind})로 그렸어요."}
    return {"vega_lite": redrawn, "kind": kind, "note": None}


def make_chart_tool() -> BaseTool:
    """make_chart 툴을 만든다. 상태가 없어서 인자 없이 만든다."""

    @tool("make_chart", response_format="content_and_artifact")
    def make_chart(table_key: str, chart: dict | None = None, chart_type: str | None = None) -> tuple[dict, dict]:
        """summarize_stat이 돌려준 차트 데이터(ChartSpec)를 Vega-Lite spec으로 그린다.
        table_key는 그 차트를 만든 표의 key, chart_type은 사용자가 요청한 종류(bar | line | pie, 없으면 실전 1이 고른 종류)다."""
        out = draw(ChartSpec.model_validate(chart) if chart else None, chart_type)
        # content: 채점·LLM용 요약 (Vega-Lite 전체는 크므로 그렸는지만), artifact: 노드가 쓰는 전체 결과
        return {"table_key": table_key, "chart_type": chart_type, "kind": out["kind"],
                "drawn": out["vega_lite"] is not None, "note": out["note"]}, out

    return make_chart
