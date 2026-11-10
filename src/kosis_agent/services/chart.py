"""차트 서비스: 실전 1의 ChartSpec을 Vega-Lite v5 spec으로 바꾼다 (T3-23).

변환 규칙(막대·꺾은선·원형 등)은 실전 1 kosis_chat.chart.vega.to_vega가 갖고 있고, 여기서는 다시 짜지 않는다.
차트 종류 자동 선택(시간 변화 = 꺾은선, 항목 비교 = 막대 …)은 T3-02 범위다.

두 가지 입구가 있다.
- to_vega_lite(spec): 워크플로 노드가 직접 부르는 함수. 차트는 실전 1이 답할 때 함께 주므로 매 턴 툴을 부를
  필요가 없다. 매 턴 make_chart 툴을 부르면 하네스가 기대 툴 목록에 없는 호출(extra_call)로 감점한다.
- make_chart 툴: 사용자가 "막대로 다시 그려 줘"처럼 차트만 요청할 때나, LLM이 툴을 고르는 아키텍처(T3-07 ReAct)에서 쓴다.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from kosis_ai.schemas import ChartSpec
from kosis_chat.chart.vega import to_vega


def to_vega_lite(spec: ChartSpec | None) -> dict | None:
    """ChartSpec → Vega-Lite dict. 차트가 없거나 실전 1이 모르는 종류면 None (리팩토링 전 chat_result와 같은 규칙)."""
    return to_vega(spec) if spec is not None else None


def make_chart_tool() -> BaseTool:
    """make_chart 툴을 만든다. 상태가 없어서 인자 없이 만든다."""

    @tool("make_chart")
    def make_chart(table_key: str, chart: dict) -> dict | None:
        """summarize_stat이 돌려준 차트 데이터(ChartSpec)를 Vega-Lite spec으로 바꾼다.
        table_key는 그 차트를 만든 표의 key다(어떤 표의 차트인지 기록용)."""
        return to_vega_lite(ChartSpec.model_validate(chart))

    return make_chart
