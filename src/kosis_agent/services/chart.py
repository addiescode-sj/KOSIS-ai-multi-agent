"""차트 서비스: 실전 1의 ChartSpec을 Vega-Lite v5 spec으로 바꾼다 (T3-23, T3-27에서 chart_type, T3-02에서 종류 규칙).

그리는 법(막대·꺾은선·원형 등)은 실전 1 kosis_chat.chart.vega.to_vega가 갖고 있고, 여기서는 다시 짜지 않는다.
T3-02(plans/T3-02.md, 옵션 A)는 종류만 정한다. 숫자는 실전 1이 계산한 사실 그대로다.
- 자동 선택(refine): 실전 1이 계산 패턴별로 고른 종류를 표 하나(AUTO_KIND)로 덮어쓴다. 시간 변화 = 꺾은선,
  항목 비교 = 막대, 구성비 = 원형, 구성비 변화 = 누적막대.
- 요청 가드(draw): 사용자가 말한 종류를 그 데이터가 담을 수 없으면(한 전체의 부분이 아닌데 원형, 시점 축이 없는데
  꺾은선) 원래 종류로 그리고 그 이유를 note로 남긴다.

두 가지 입구가 있다. 둘 다 refine을 거친다.
- to_vega_lite(spec): 워크플로 노드가 직접 부르는 함수. 사용자가 차트를 요청하지 않은 턴에는 실전 1이 답과 함께 준
  차트를 그린다. 이때 make_chart 툴을 부르면 하네스가 기대 툴 목록에 없는 호출(extra_call)로 감점한다.
- make_chart 툴: 사용자가 차트를 요청했을 때("그 표로 막대그래프", "그래프로도 보여줘") 노드가 부른다.
  인자 chart_type은 하네스 채점 필드(evals/schemas.ARG_FIELDS)다. T3-07 ReAct에서도 같은 툴을 쓴다.
"""

from __future__ import annotations

import re

from langchain_core.tools import BaseTool, tool

from kosis_ai.schemas import ChartSpec
from kosis_chat.chart.vega import HIGHLIGHT, OTHER, _base, _unit_title, register_kind, to_vega
from kosis_chat.shared.numbers import format_number

# 직전 답에 차트 데이터가 없을 때의 답 (코드 고정 문장)
NO_CHART_TEXT = "이전 답에는 그릴 수 있는 차트 데이터가 없어요. 숫자를 먼저 물어봐 주세요."
CHART_NAMES = {"bar": "막대그래프", "line": "꺾은선 그래프", "pie": "원형 차트"}
KIND_NAMES = {"bar": "막대그래프", "timeline": "기간 막대그래프", "line": "꺾은선 그래프", "multi_line": "꺾은선 그래프",
              "grouped_bar": "묶음 막대그래프", "pie": "원형 차트", "stacked_bar": "누적 막대그래프"}

# 자동 선택 표: (실전 1 계산 패턴, 실전 1이 고른 종류) → 그릴 종류. 표에 없으면 실전 1 종류 그대로.
AUTO_KIND = {
    ("extreme", "timeline"): "line",  # 한 대상의 시점별 최댓값·최솟값: 시간 변화
    ("avg", "timeline"): "line",  # 기간 평균: 그 기간의 시간 변화를 보인다
    ("sum", "timeline"): "line",  # 기간 합계: 같은 이유
    ("shr", "bar"): "stacked_bar",  # 두 시점의 구성비: 부분과 나머지(100%)를 시점마다
}
# 시점 축으로 읽는 종류 (x가 시점)
TIME_KINDS = ("line", "timeline", "multi_line", "grouped_bar", "stacked_bar")
# 실전 1이 시점 라벨을 쓰는 모양: 시점 코드 그대로('2015', '202406', 비교 막대) 또는 period_text·span_text
# ('2015년', '2019년~2023년 평균', 구성비 변화·기간 묶음 비교)
_PERIOD_LABEL = re.compile(r"^(\d{4}(\d{2})?|\d{4}년( \d{1,2}월| [상하]반기| \d분기)?(~(\d{4}년)?( ?\d{1,2}월)?)?( 평균| 합계)?)$")
REST_NAME = "그 외"  # 원형 차트의 나머지 조각과 같은 이름 (실전 1 builders._share)


def refine(spec: ChartSpec) -> ChartSpec:
    """실전 1이 고른 종류를 AUTO_KIND로 바꾼다. 바꾸면 vega_lite를 비워 다시 그리게 한다."""
    kind = AUTO_KIND.get((spec.pattern or "", spec.kind))
    if kind is None:
        return spec
    if kind == "stacked_bar" and (spec.unit != "%" or any(p.value is None or not 0 <= p.value <= 100 for p in spec.points())):
        return spec  # 비중(%)이 아니면 나머지를 만들 수 없다
    return spec.model_copy(update={"kind": kind, "vega_lite": None})


def to_vega_lite(spec: ChartSpec | None) -> dict | None:
    """ChartSpec → Vega-Lite dict. 차트가 없거나 실전 1이 모르는 종류면 None (리팩토링 전 chat_result와 같은 규칙)."""
    return to_vega(refine(spec)) if spec is not None else None


def has_time_axis(spec: ChartSpec) -> bool:
    """x축이 시점인가: 시점 종류이거나, 막대의 이름이 모두 시점 라벨(같은 대상의 두 시점 비교 등)."""
    return spec.kind in TIME_KINDS or all(_PERIOD_LABEL.match(p.label) for p in spec.points())


def is_composition(spec: ChartSpec) -> bool:
    """한 전체를 나눈 부분인가: 실전 1 share 패턴(원형, 나머지 조각 포함)만."""
    return spec.pattern == "share" and spec.kind == "pie"


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


def refusal_note(spec: ChartSpec, requested: str) -> str | None:
    """요청 종류를 이 데이터로 그릴 수 없는 이유 (그릴 수 있으면 None)."""
    shown = KIND_NAMES.get(spec.kind, spec.kind)
    if requested == "pie" and not is_composition(spec):
        return f"원형 차트는 한 전체를 나눈 부분(구성비)일 때만 그려요. 이 값들은 한 전체의 부분이 아니라서 {shown}로 그렸어요."
    if requested == "line" and not has_time_axis(spec):
        return f"꺾은선 그래프는 시점이 이어지는 값에만 그려요. 이 차트는 시점이 아니라 항목을 비교해서 {shown}로 그렸어요."
    return None


def _with_periods(spec: ChartSpec) -> ChartSpec:
    """꺾은선은 점의 period를 x로 쓴다. 시점 라벨만 있는 막대(두 시점 비교)를 꺾은선으로 그릴 때 라벨을 period로 채운다."""
    series = [s.model_copy(update={"points": [p if p.period else p.model_copy(update={"period": p.label}) for p in s.points]})
              for s in spec.series]
    return spec.model_copy(update={"series": series})


def draw(spec: ChartSpec | None, chart_type: str | None) -> dict:
    """차트 하나를 그린다. 결과: {"vega_lite", "kind"(실제로 그린 종류), "note"(요청대로 못 그렸을 때 이유)}."""
    if spec is None:
        return {"vega_lite": None, "kind": None, "note": NO_CHART_TEXT}
    spec = refine(spec)
    if chart_type is None:
        return {"vega_lite": to_vega(spec), "kind": spec.kind, "note": None}
    if (note := refusal_note(spec, chart_type)) is not None:
        return {"vega_lite": to_vega(spec), "kind": spec.kind, "note": note}
    kind = phase1_kind(chart_type, spec.kind)
    target = spec.model_copy(update={"kind": kind, "vega_lite": None})  # vega_lite가 있으면 to_vega가 그대로 쓰므로 비운다
    redrawn = to_vega(_with_periods(target) if kind == "line" else target)
    if redrawn is None:  # 실전 1이 그 종류를 그리지 못함 → 원래 종류로 그리고 사실을 남긴다
        return {"vega_lite": to_vega(spec), "kind": spec.kind,
                "note": f"{CHART_NAMES.get(chart_type, chart_type)}로는 그릴 수 없어 원래 종류({spec.kind})로 그렸어요."}
    return {"vega_lite": redrawn, "kind": kind, "note": None}


def _draw_stacked(spec: ChartSpec) -> dict:
    """구성비 변화: 시점마다 100% 막대 하나, 부분(실전 1 비중 사실 그대로)과 나머지(100 − 비중, 코드가 계산)."""
    part = spec.title.removesuffix(" 비교").removesuffix(" 비중").strip() or "해당"
    rows = []
    for p in spec.points():
        rest = round(100 - p.value, 1)
        rows.append({"시점": p.label, "구분": part, "값": p.value, "표시": p.display, "순서": 0})
        rows.append({"시점": p.label, "구분": REST_NAME, "값": rest, "표시": format_number(rest, "%", 1), "순서": 1})
    out = _base(spec.title, rows)
    out["mark"] = {"type": "bar"}
    out["encoding"] = {
        "x": {"field": "시점", "type": "ordinal", "title": None, "sort": None, "axis": {"labelAngle": 0}},
        "y": {"field": "값", "type": "quantitative", "title": _unit_title(spec.unit), "stack": "zero",
              "scale": {"domain": [0, 100]}},
        "color": {"field": "구분", "type": "nominal", "title": None, "sort": [part, REST_NAME],
                  "scale": {"domain": [part, REST_NAME], "range": [HIGHLIGHT, OTHER]}},
        "order": {"field": "순서", "type": "quantitative"},
        "tooltip": [{"field": "시점", "type": "ordinal"}, {"field": "구분", "type": "nominal"},
                    {"field": "표시", "type": "nominal", "title": "값"}],
    }
    return out


# 실전 1의 그리는 표(kosis_chat.chart.vega.DRAW)에 누적막대를 더한다. import 시점에 실전 1 전역 표가 바뀌지만,
# 실전 1은 이 종류를 만들지 않으므로 실전 1 단독 결과는 그대로다.
register_kind("stacked_bar", _draw_stacked)


def make_chart_tool() -> BaseTool:
    """make_chart 툴을 만든다. 상태가 없어서 인자 없이 만든다."""

    @tool("make_chart", response_format="content_and_artifact")
    def make_chart(table_key: str, chart: dict | None = None, chart_type: str | None = None) -> tuple[dict, dict]:
        """summarize_stat이 돌려준 차트 데이터(ChartSpec)를 Vega-Lite spec으로 그린다.
        table_key는 그 차트를 만든 표의 key, chart_type은 사용자가 요청한 종류(bar | line | pie, 없으면 자동 선택)다."""
        out = draw(ChartSpec.model_validate(chart) if chart else None, chart_type)
        # content: 채점·LLM용 요약 (Vega-Lite 전체는 크므로 그렸는지만), artifact: 노드가 쓰는 전체 결과
        return {"table_key": table_key, "chart_type": chart_type, "kind": out["kind"],
                "drawn": out["vega_lite"] is not None, "note": out["note"]}, out

    return make_chart
