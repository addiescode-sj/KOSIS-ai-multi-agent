"""⑤ 차트 에이전트: 그림만 그린다. 숫자는 ④가 준 ChartSpec 그대로다.

- 사용자가 차트를 요청한 턴: make_chart 툴(요청한 종류).
- 요청하지 않은 턴: 실전 1이 준 ChartSpec을 to_vega_lite로 바꾼다(툴 아님: 요청 없는 툴 호출은 extra_call 감점).
- 차트만 다시 그리는 턴: 직전 답의 차트 데이터로.
TODO(S4): "그릴지·종류" 판단을 ④와 병렬로 떼어 내기 (지금은 판단이 ①의 chart_request에 들어 있음).
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from kosis_agent.services.chart import to_vega_lite
from kosis_multi.tools import Toolbox, call_tool


def make_chart_agent(tools: Toolbox):
    def chart(state: dict, config: RunnableConfig) -> dict:
        if state["chart_only"]:
            spec, key = state.get("active_chart"), state.get("active_table")
        else:
            spec, key = state["stat"].chart, state["table_key"]
        if not state["chart_requested"]:
            return {"chart": to_vega_lite(spec)}
        args = {"table_key": key, "chart": spec.model_dump(mode="json") if spec else None,
                "chart_type": state.get("chart_type")}
        out = call_tool(tools.make_chart, args, config).artifact
        return {"chart": out["vega_lite"], "chart_note": out["note"]}

    return chart
