"""⑤ 차트 에이전트: 그림만 그린다. 숫자는 ④가 준 ChartSpec 그대로다.

- 사용자가 차트를 요청한 턴: make_chart 툴(요청한 종류).
- 요청하지 않은 턴: 실전 1이 준 ChartSpec을 chart_payload로 그대로 보낸다(툴 아님: 요청 없는 툴 호출은 extra_call 감점).
- 응답 차트는 ChartSpec JSON이다(T3-02: Vega-Lite 없음, 프런트가 Recharts로 그림).
- 차트만 다시 그리는 턴: 직전 답의 차트 데이터로.
그릴지·종류 판단은 ①의 chart_request가 한다. ④의 ChartSpec이 있어야 그리므로 ④ 뒤에 돈다(차트는 0 ms라 병렬 이득 없음).
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from kosis_agent.services.chart import chart_payload
from kosis_multi.tools import Toolbox, call_tool


def make_chart_agent(tools: Toolbox):
    def chart(state: dict, config: RunnableConfig) -> dict:
        if state["chart_only"]:
            spec, key = state.get("active_chart"), state.get("active_table")
        else:
            spec, key = state["stat"].chart, state["table_key"]
        if not state["chart_requested"]:
            return {"chart": chart_payload(spec)}
        args = {"table_key": key, "chart": spec.model_dump(mode="json") if spec else None,
                "chart_type": state.get("chart_type")}
        out = call_tool(tools.make_chart, args, config).artifact
        return {"chart": out["chart"], "chart_note": out["note"]}

    return chart
