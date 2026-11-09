"""메인 그래프 (T3-23 1단계): START → search → (refuse: END | found: statistics → END).

리팩토링 전 graph.py와 경로가 같다. 바뀐 것은 다음과 같다.
- supervisor 노드(아무것도 하지 않던 no-op)를 지웠다.
- 노드 이름: search_agent → search, stat_agent → statistics.
- 노드가 실전 1·2를 직접 부르지 않고 LangChain 툴(search_tables · summarize_stat)을 거친다. 툴을 거치면
  on_tool_start/on_tool_end 콜백이 불려서 `execute_tool <name>` span이 생기고, T3-06 agent 계층이 툴 이름·인자·
  결과로 그래프를 채점할 수 있다(plans/agent-graph-refactor.md P2).
- checkpointer를 받을 수 있다. 서비스와 평가는 thread_id별로 대화를 잇는다.

실전 2 검색 그래프와 실전 1 답변 그래프는 각 툴 안에서 통째로 돈다. 바깥 그래프는 최종 결과(SearchOutput,
StatOutput)만 받는다. 상태 스키마가 서로 다른 그래프를 노드 안에서 부르는 것은 LangGraph가 안내하는 방식이다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph

from kosis_agent.graph.state import AgentState
from kosis_agent.retrieval.search import make_search_tables_tool
from kosis_agent.services.statistics import make_summarize_stat_tool
from kosis_chat.api import summarize_stat_run
from kosis_chat.schemas.io import StatOutput
from kosis_chat.schemas.resources import LLMConfig as StatLLMConfig
from kosis_rag.schemas.io import SearchOutput


def call_tool(tool: BaseTool, args: dict, config: RunnableConfig) -> ToolMessage:
    """노드에서 툴을 ToolCall 형태로 부른다.

    ToolCall(id 포함)로 불러야 content_and_artifact 툴이 ToolMessage(content, artifact)를 돌려준다. dict 인자만
    넘기면 artifact가 버려진다. 노드의 config를 넘겨야 이 턴의 콜백(OTel span) 아래에서 툴 span이 열린다.
    """
    call = {"name": tool.name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}
    return tool.invoke(call, config)


def build_graph(search: Any, stat_llm: StatLLMConfig, *, retry_interval: float = 0.0,
                summarize: Callable[..., StatOutput] = summarize_stat_run, checkpointer: Any = None):
    """메인 그래프를 compile한다.

    In: search = kosis_rag.api.SearchModule (테스트·stat_oracle에서는 .stream(SearchInput)만 있는 가짜);
        stat_llm = 실전 1의 LLMConfig (실전 2 LLMConfig와 다른 클래스·필드);
        retry_interval = 실전 1의 HCX 429 재시도 대기 초; summarize = 실전 1 summarize_stat_run (테스트에서는 가짜);
        checkpointer = LangGraph checkpointer (None이면 턴 사이 상태를 잇지 않음).
    Out: AgentState 위의 compiled graph. stream_mode "custom"으로 돌리면 실전 2 노드마다 (노드 이름, trace 항목),
        그다음 실전 1이 ("stat", {"seconds", "key", "spans"})로 온다(리팩토링 전과 같은 이벤트).
    Errors: 실전 1의 LLMKeyMissing·RuntimeError와 실전 2의 예외는 그대로 올라간다. 실전 1의 LookupError(표 키가
        tables.csv에 없음)는 summarize_stat 툴이 거절 답으로 바꾼다(services/statistics.py).
    """
    search_tables = make_search_tables_tool(search)
    summarize_stat = make_summarize_stat_tool(stat_llm, retry_interval=retry_interval, summarize=summarize)

    def search_node(state: AgentState, config: RunnableConfig) -> dict:
        """마지막 사용자 메시지로 표를 찾는다. 거절이면 실전 2의 거절 문장으로 이 턴을 끝낸다."""
        question = state["messages"][-1].content
        out: SearchOutput = call_tool(search_tables, {"question": question}, config).artifact
        update: dict = {"search": out, "stat": None}  # 지난 턴의 stat이 남아 이번 턴 결과로 읽히지 않게 지운다
        if out.route == "refuse":
            update["messages"] = [AIMessage(out.refusal_text or "")]
        return update

    def after_search(state: AgentState) -> str:
        out = state["search"]
        return "statistics" if out.route == "found" and out.picked else END

    def statistics_node(state: AgentState, config: RunnableConfig) -> dict:
        """검색이 고른 첫 표(picked[0].key = tables.csv 키, tbl_id 아님)로 실전 1에 답을 받는다."""
        out: SearchOutput = state["search"]
        args = {"question": out.question, "table_key": out.picked[0].key}
        stat: StatOutput = call_tool(summarize_stat, args, config).artifact
        return {"stat": stat, "messages": [AIMessage(stat.answer.text)]}

    graph = StateGraph(AgentState)
    graph.add_node("search", search_node)
    graph.add_node("statistics", statistics_node)
    graph.add_edge(START, "search")
    graph.add_conditional_edges("search", after_search, ["statistics", END])
    graph.add_edge("statistics", END)
    return graph.compile(checkpointer=checkpointer)
