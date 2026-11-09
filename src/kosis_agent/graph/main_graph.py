"""메인 그래프 (T3-23 옵션 A: 결정적 워크플로). 다음에 무엇을 할지는 LLM이 아니라 아래 조건부 경로(코드)가 정한다.

    START → context ─(지시어 + 직전 표 있음: 검색 생략)───────────────────┐
                └─(새 질문)→ search ─(refuse · 넘길 표 없음)→ finalize       │
                                └─(found)→ statistics ←─────────────────────┘
                                             ├─(표 불일치 거절 · 예산 남음)→ statistics (다음 후보 표)
                                             ├─(답함)→ chart → finalize
                                             └─(되묻기 · 질문 문제 · 예산 끝)→ finalize
    finalize → END   (outcome: answered | refused | clarify | failed)

노드별 역할:
- context    이번 턴 상태를 비우고, 질문이 직전 표를 가리키는지 규칙으로 판단한다(graph/context.py).
- search     search_tables 툴(실전 2 검색 그래프 전체)을 부른다.
- statistics summarize_stat 툴(실전 1)을 표 하나로 부른다. 표 불일치로 거절되면 다음 후보 표로 다시 들어온다.
- chart      실전 1이 준 ChartSpec을 Vega-Lite로 바꾼다(툴이 아닌 함수 호출: services/chart.py 참고).
- finalize   outcome과 답 메시지를 정한다. 답 문장은 실전 1·2가 만든 것이나 코드 고정 문장만 쓴다(LLM 문장 생성 없음).

노드는 실전 1·2를 직접 부르지 않고 LangChain 툴을 거친다. 그래서 `execute_tool <name>` span이 생기고, T3-06 agent
계층이 툴 이름·인자·결과로 그래프를 채점할 수 있다(plans/agent-graph-refactor.md P2). 실전 1·2 그래프는 각 툴 안에서
통째로 돈다. 상태 스키마가 서로 다른 그래프를 노드 안에서 부르는 것은 LangGraph가 안내하는 방식이다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph

from kosis_agent.graph.context import refers_to_previous_table
from kosis_agent.graph.state import AgentState
from kosis_agent.retrieval.search import make_search_tables_tool
from kosis_agent.services.chart import to_vega_lite
from kosis_agent.services.statistics import make_summarize_stat_tool
from kosis_chat.api import summarize_stat_run
from kosis_chat.schemas.io import StatOutput
from kosis_chat.schemas.resources import LLMConfig as StatLLMConfig
from kosis_rag.parse.node import parse_query
from kosis_rag.parse.schemas import ParsedQuery
from kosis_rag.schemas.io import SearchOutput

# summarize_stat의 조건 인자로 넘기는 ParsedQuery 필드 (= evals/schemas.ARG_FIELDS 중 summarize_stat에 해당하는 것)
CONDITION_FIELDS = ("period_start", "period_end", "regions", "dimensions")

# 실전 1이 이 이유로 거절하면 "질문은 괜찮은데 이 표가 맞지 않는다"는 뜻이라 다음 후보 표를 시도해 볼 만하다.
#   no_period(요청 기간이 표에 없음) · no_item(요청 항목이 표에 없음) · class_mismatch(분류가 그 수준·시점에 없음)
#   · out_of_scope(표 범위 밖. 표 키가 tables.csv에 없을 때도 이 이유: services/statistics.py)
# 나머지 이유(causal: 인과 질문, unit_mismatch, not_aggregatable, missing_value: 셀이 '-'·'X')는 질문이나 셀 자체의
# 문제라서, 표를 바꿔도 같은 답이 나오거나 다른 정의의 숫자를 섞게 된다. 그래서 다시 시도하지 않는다.
TABLE_MISMATCH = frozenset({"no_period", "no_item", "class_mismatch", "out_of_scope"})

# 검색은 found인데 실전 1에 넘길 표가 하나도 없을 때의 답 (코드 고정 문장)
NO_TABLE_TEXT = "질문에 맞는 통계표를 고르지 못했어요. 지표·지역·기간을 조금 더 구체적으로 물어봐 주세요."


def call_tool(tool: BaseTool, args: dict, config: RunnableConfig) -> ToolMessage:
    """노드에서 툴을 ToolCall 형태로 부른다.

    ToolCall(id 포함)로 불러야 content_and_artifact 툴이 ToolMessage(content, artifact)를 돌려준다. dict 인자만
    넘기면 artifact가 버려진다. 노드의 config를 넘겨야 이 턴의 콜백(OTel span) 아래에서 툴 span이 열린다.
    """
    call = {"name": tool.name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}
    return tool.invoke(call, config)


def candidate_keys(search: SearchOutput | None) -> list[str]:
    """실전 1에 넘길 수 있는 표 키, 시도할 순서대로: 실전 2가 고른 표(picked) 다음 나머지 후보(candidates), 중복 없이.

    키는 실전 2 결과 안에서만 고른다. 그래프가 표 키를 새로 만들지 않는다(표 ID 보존 원칙).
    """
    if search is None:
        return []
    return list(dict.fromkeys(c.key for c in [*search.picked, *search.candidates]))


def default_parser(search: Any) -> Callable[[str], ParsedQuery]:
    """실전 2 검색이 쓰는 것과 같은 질의 해석 규칙(parse_query + 시군구 사전).

    시군구 사전은 SearchModule.resources.sigungu에 이미 열려 있다(실전 2 parse 노드도 이것을 쓴다). 사전이 없는
    가짜 검색(테스트)에서는 시군구 없이 읽는다(시도 이름·연도·분류는 그대로 읽힘).
    """
    sigungu = getattr(getattr(search, "resources", None), "sigungu", None)
    return lambda question: parse_query(question, sigungu=sigungu)


def build_graph(search: Any, stat_llm: StatLLMConfig, *, retry_interval: float = 0.0,
                summarize: Callable[..., StatOutput] = summarize_stat_run, checkpointer: Any = None,
                alt_table_budget: int = 0, parse: Callable[[str], ParsedQuery] | None = None):
    """메인 그래프를 compile한다.

    In: search = kosis_rag.api.SearchModule (테스트·stat_oracle에서는 .stream(SearchInput)만 있는 가짜);
        stat_llm = 실전 1의 LLMConfig (실전 2 LLMConfig와 다른 클래스·필드);
        retry_interval = 실전 1의 HCX 429 재시도 대기 초; summarize = 실전 1 summarize_stat_run (테스트에서는 가짜);
        checkpointer = LangGraph checkpointer (None이면 턴 사이 상태를 잇지 않음, graph/state.new_checkpointer 권장);
        alt_table_budget = 첫 표가 표 불일치로 거절됐을 때 더 시도할 후보 표 수. 기본 0(끔) = 1단계와 같은 경로.
            기본을 끈 이유: T3-22 실제 검색 순위를 재생한 측정(plans/T3-23.md Result)에서 재시도 2건 중 1건이
            정답 표가 아닌 표로 "답함"이 됐다(거절 → 틀린 표의 숫자). 데이터 정확성이 먼저라서, HCX·실시간 검색으로
            충분한 표본을 잰 뒤에 켤지 정한다.
        parse = 질문 → ParsedQuery (None이면 default_parser(search)). summarize_stat의 조건 인자를 채운다.
    Out: AgentState 위의 compiled graph. stream_mode "custom"으로 돌리면 실전 2 노드마다 (노드 이름, trace 항목),
        그다음 실전 1 시도마다 ("stat", {"seconds", "key", "spans"})가 온다.
    Errors: 실전 1의 LLMKeyMissing·RuntimeError와 실전 2의 예외는 그대로 올라간다(SSE `error`). 실전 1의
        LookupError(표 키가 tables.csv에 없음)는 summarize_stat 툴이 out_of_scope 거절로 바꾸므로, 다음 후보 표로 넘어간다.
    """
    if alt_table_budget < 0:
        raise ValueError("alt_table_budget cannot be negative")
    search_tables = make_search_tables_tool(search)
    summarize_stat = make_summarize_stat_tool(stat_llm, retry_interval=retry_interval, summarize=summarize)
    parse = parse or default_parser(search)

    def context_node(state: AgentState) -> dict:
        """이번 턴 상태를 비우고, 질문이 직전 표("그 표")를 가리키는지 정한다."""
        question = state["messages"][-1].content
        reuse = bool(state.get("active_table")) and refers_to_previous_table(question)
        return {"question": question, "reused_table": reuse, "attempts": [], "stat": None, "table_key": None,
                "chart": None, "outcome": None, "degraded": False}

    def after_context(state: AgentState) -> str:
        return "statistics" if state["reused_table"] else "search"

    def search_node(state: AgentState, config: RunnableConfig) -> dict:
        """이번 질문으로 표를 찾는다. 2차 리랭커가 실패했으면 degraded로 표시한다(실전 2는 조용히 1차 순서로 대신함)."""
        out: SearchOutput = call_tool(search_tables, {"question": state["question"]}, config).artifact
        degraded = any(s.get("node") == "rerank2" and s.get("stage2_error") for s in out.spans)
        return {"search": out, "degraded": degraded}

    def after_search(state: AgentState) -> str:
        out = state["search"]
        return "statistics" if out.route == "found" and candidate_keys(out) else "finalize"

    def next_table(state: AgentState) -> str | None:
        """다음에 실전 1에 넘길 표 키. 지시어 턴은 직전 표 하나만 쓴다(사용자가 가리킨 표를 몰래 바꾸지 않음)."""
        tried = {a["key"] for a in state["attempts"]}
        if state["reused_table"]:
            return None if tried else state["active_table"]
        return next((k for k in candidate_keys(state["search"]) if k not in tried), None)

    def statistics_node(state: AgentState, config: RunnableConfig) -> dict:
        """표 하나로 실전 1에 답을 받는다. 숫자는 실전 1의 코드가 계산한다.

        툴 인자 = 질문 + 표 키 + 조건 4개(기간·시도·분류). 조건은 실전 2와 같은 규칙으로 이번 질문에서 읽는다(LLM 아님).
        지시어 턴("그 표에서 부산은?")도 이번 질문을 읽으므로, 바뀐 조건(부산)이 인자에 그대로 드러난다.
        """
        key = next_table(state)
        parsed = parse(state["question"])
        args = {"question": state["question"], "table_key": key,
                **{field: getattr(parsed, field) for field in CONDITION_FIELDS}}
        stat: StatOutput = call_tool(summarize_stat, args, config).artifact
        answer = stat.answer
        attempts = [*state["attempts"], {"key": key, "outcome": answer.outcome,
                                         "reason": answer.refusal.reason if answer.refusal else None}]
        # 턴은 마지막으로 시도한 표에서 끝난다. 대체 표로 다시 들어오면 그 표의 결과로 덮어쓴다.
        # 평가 하네스도 "마지막 summarize_stat 호출"을 이 턴의 표·답으로 채점하므로 둘이 같은 기준이다.
        return {"attempts": attempts, "stat": stat, "table_key": key}

    def after_statistics(state: AgentState) -> str:
        last = state["attempts"][-1]
        if last["outcome"] == "answered":
            return "chart"
        retry = (last["outcome"] == "refused" and last["reason"] in TABLE_MISMATCH
                 and len(state["attempts"]) <= alt_table_budget and next_table(state) is not None)
        return "statistics" if retry else "finalize"

    def chart_node(state: AgentState) -> dict:
        return {"chart": to_vega_lite(state["stat"].chart)}

    def finalize_node(state: AgentState) -> dict:
        """outcome과 답 메시지를 정한다. 답한 표는 다음 턴의 "그 표"(active_table)가 된다."""
        stat: StatOutput | None = state.get("stat")
        if stat is None:  # 실전 1까지 가지 못함: 검색 거절, 또는 넘길 표 없음
            out: SearchOutput = state["search"]
            if out.route == "refuse":
                return {"outcome": "refused", "messages": [AIMessage(out.refusal_text or "")]}
            return {"outcome": "failed", "messages": [AIMessage(NO_TABLE_TEXT)]}
        return {"outcome": stat.answer.outcome, "active_table": state["table_key"],
                "messages": [AIMessage(stat.answer.text)]}

    graph = StateGraph(AgentState)
    graph.add_node("context", context_node)
    graph.add_node("search", search_node)
    graph.add_node("statistics", statistics_node)
    graph.add_node("chart", chart_node)
    graph.add_node("finalize", finalize_node)
    graph.add_edge(START, "context")
    graph.add_conditional_edges("context", after_context, ["search", "statistics"])
    graph.add_conditional_edges("search", after_search, ["statistics", "finalize"])
    graph.add_conditional_edges("statistics", after_statistics, ["statistics", "chart", "finalize"])
    graph.add_edge("chart", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
