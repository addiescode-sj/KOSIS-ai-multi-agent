"""차트 서비스: 실전 1 차트 모듈(kosis_chat.chart.module)을 그래프 노드와 make_chart 툴로 잇는 얇은 껍데기 (T3-02).

차트 종류 결정(자동 선택 표·요청 종류·형태 검사·누적막대·점 상한)은 모두 실전 1 T1-32에 있다. 여기서는 다시 짜지 않는다.
Vega-Lite는 쓰지 않는다(KOS-202). 응답의 차트는 그린 ChartSpec의 JSON(chart_data)이고, 프런트가 Recharts로 바로 그린다.

두 가지 입구가 있다.
- chart_payload(spec): 사용자가 차트를 요청하지 않은 턴. 실전 1이 답과 함께 준 차트(이미 종류가 정해짐)를 그대로 보낸다.
  이때 make_chart 툴을 부르면 하네스가 기대 툴 목록에 없는 호출(extra_call)로 감점한다.
- make_chart 툴: 사용자가 차트를 요청했을 때("그 표로 막대그래프", "그래프로도 보여줘") 노드가 부른다. 실전 1 redraw가
  요청 종류로 다시 그리고, 그 데이터로 그릴 수 없으면 다른 종류로 그린 뒤 이유를 note로 남긴다.
  인자 chart_type은 하네스 채점 필드(evals/schemas.ARG_FIELDS)다. T3-07 ReAct에서도 같은 툴을 쓴다.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from kosis_ai.schemas import ChartSpec
from kosis_chat.chart.module import chart_data, redraw

# 직전 답에 차트 데이터가 없을 때의 답 (코드 고정 문장)
NO_CHART_TEXT = "이전 답에는 그릴 수 있는 차트 데이터가 없어요. 숫자를 먼저 물어봐 주세요."
CHART_NAMES = {"bar": "막대그래프", "line": "꺾은선 그래프", "pie": "원형 차트", "stacked": "누적 막대그래프"}


def chart_payload(spec: ChartSpec | None) -> dict | None:
    """요청 없는 턴의 차트: 실전 1이 그린 ChartSpec의 JSON (차트가 없으면 None)."""
    return chart_data(spec)


def draw(spec: ChartSpec | None, chart_type: str | None) -> dict:
    """차트 하나를 요청 종류로 다시 그린다. 결과: {"chart"(그린 차트 데이터 = chart_data, 없으면 None), "kind"(실제로
    그린 종류), "note"(요청대로 못 그렸거나 일부만 그렸을 때 사용자 안내), "reasons"(코드 판단 기록)}."""
    if spec is None:
        return {"chart": None, "kind": None, "note": NO_CHART_TEXT, "reasons": ["no_spec"]}
    out = redraw(spec, chart_type)
    return {"chart": chart_data(out.spec) if out.drawn else None, "kind": out.kind, "note": out.note,
            "reasons": out.reasons}


def make_chart_tool() -> BaseTool:
    """make_chart 툴을 만든다. 상태가 없어서 인자 없이 만든다."""

    @tool("make_chart", response_format="content_and_artifact")
    def make_chart(table_key: str, chart: dict | None = None, chart_type: str | None = None) -> tuple[dict, dict]:
        """summarize_stat이 돌려준 차트 데이터(ChartSpec)를 요청한 종류로 다시 그린다.
        table_key는 그 차트를 만든 표의 key, chart_type은 사용자가 요청한 종류(bar | line | pie | stacked, 없으면 그 차트 종류)다."""
        out = draw(ChartSpec.model_validate(chart) if chart else None, chart_type)
        # content: 채점·LLM용 요약 (차트 데이터 전체는 크므로 그렸는지만), artifact: 노드가 쓰는 전체 결과
        return {"table_key": table_key, "chart_type": chart_type, "kind": out["kind"],
                "drawn": out["chart"] is not None, "note": out["note"]}, out

    return make_chart
