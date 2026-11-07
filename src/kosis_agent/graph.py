"""Agent graph over the refactored phase 1 / phase 2 modules: supervisor -> search_agent -> [stat_agent] -> END.

search_agent runs the phase 2 search module (kosis_rag.api.SearchModule, its graph ends at gate) unchanged. On
refuse the answer is the module's refusal_text (code text, no LLM). On found, stat_agent hands the question and
picked[0].key (the tables.csv key "<org_id>_<tbl_id>", not tbl_id) to phase 1 summarize_stat_run, which answers
(answered | refused | clarify, numbers written by code) and gives the chart (ChartSpec -> Vega-Lite).
make_chart (T3-02) and the tool-choosing loop (T3-03) build on this; T3-07~09 compare architectures on it.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph
from pydantic import BaseModel, Field

from kosis_ai.schemas import StatAnswer
from kosis_chat.api import summarize_stat_run
from kosis_chat.chart.vega import to_vega
from kosis_chat.schemas.io import StatOutput
from kosis_chat.schemas.resources import LLMConfig as StatLLMConfig
from kosis_rag.schemas.io import SearchInput, SearchOutput, TableCandidate


class AgentState(MessagesState):
    """The conversation plus the last turn's search and stat results (the tables later turns refer to, T3-04)."""

    search: SearchOutput | None
    stat: StatOutput | None


class ChatResult(BaseModel):
    """The `result` event of /chat/stream: one turn's answer, its tables and its chart.

    The first fields keep the names the frontend reads from the old phase 2 SearchResult (question, candidates,
    picked, no_answer, no_answer_reason, answer, answer_origin), so the T3-00 UI shows the answer as before.
    """

    question: str
    route: str  # "refuse" (search gate) | "found"
    candidates: list[TableCandidate] = Field(default_factory=list)
    picked: list[TableCandidate] = Field(default_factory=list)
    no_answer: bool  # search refused, or phase 1 answered refused / clarify
    no_answer_reason: str | None = None  # search refusal_reason, or phase 1 refusal.reason
    answer: str
    answer_origin: str  # "refusal" (search gate text) | "stat" (phase 1 StatAnswer.text)
    table_key: str | None = None  # the key handed to phase 1
    stat_answer: StatAnswer | None = None
    chart: dict[str, Any] | None = None  # Vega-Lite v5 (kosis_chat.chart.vega.to_vega), None when no chart


def chat_result(state: dict) -> ChatResult:
    """The ChatResult of a final AgentState."""
    search: SearchOutput = state["search"]
    stat: StatOutput | None = state.get("stat")
    base = {"question": search.question, "route": search.route, "candidates": search.candidates,
            "picked": search.picked}
    if stat is None:  # refused at the search gate
        return ChatResult(**base, no_answer=True, no_answer_reason=search.refusal_reason,
                          answer=search.refusal_text or "", answer_origin="refusal")
    answer = stat.answer
    return ChatResult(
        **base,
        no_answer=answer.outcome != "answered",
        no_answer_reason=answer.refusal.reason if answer.refusal else None,
        answer=answer.text,
        answer_origin="stat",
        table_key=search.picked[0].key,
        stat_answer=answer,
        chart=to_vega(stat.chart) if stat.chart is not None else None,
    )


def _writer():
    try:
        return get_stream_writer()
    except RuntimeError:  # invoked outside a stream
        return None


def build_graph(search, stat_llm: StatLLMConfig, *, retry_interval: float = 0.0,
                summarize: Callable[..., StatOutput] = summarize_stat_run):
    """Compile START -> supervisor -> search_agent -> (refuse: END | found: stat_agent -> END).

    In: search = a kosis_rag.api.SearchModule (anything with .stream(SearchInput) in tests);
        stat_llm = phase 1's LLMConfig (not phase 2's: different class and fields);
        summarize = phase 1 summarize_stat_run (a fake in tests).
    Out: a compiled graph over AgentState. Streamed with stream_mode "custom", each phase 2 node arrives as
        (node name, its trace entry with seconds), then phase 1 as ("stat", {"seconds", "key", "spans"}).
    Errors: phase 1 LookupError (key not in tables.csv / KOSIS_WORKSPACE), LLMKeyMissing, RuntimeError (model
        call failed) propagate to the caller.
    """

    def supervisor(state: AgentState) -> dict:
        return {}

    def search_agent(state: AgentState) -> dict:
        writer = _writer()
        out = None
        for name, value in search.stream(SearchInput(question=state["messages"][-1].content)):
            if name == "output":
                out = value
            elif writer:
                writer((name, value))
        update: dict = {"search": out, "stat": None}
        if out.route == "refuse":
            update["messages"] = [AIMessage(out.refusal_text or "")]
        return update

    def after_search(state: AgentState) -> str:
        out = state["search"]
        return "stat_agent" if out.route == "found" and out.picked else END

    def stat_agent(state: AgentState) -> dict:
        out: SearchOutput = state["search"]
        key = out.picked[0].key
        started = time.perf_counter()
        stat = summarize(out.question, key, llm=stat_llm, retry_interval=retry_interval)
        writer = _writer()
        if writer:
            writer(("stat", {"seconds": time.perf_counter() - started, "key": key, "spans": stat.spans}))
        return {"stat": stat, "messages": [AIMessage(stat.answer.text)]}

    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor)
    graph.add_node("search_agent", search_agent)
    graph.add_node("stat_agent", stat_agent)
    graph.add_edge(START, "supervisor")
    graph.add_edge("supervisor", "search_agent")  # ponytail: one route until the supervisor picks tools (T3-03 / T3-09)
    graph.add_conditional_edges("search_agent", after_search, ["stat_agent", END])
    graph.add_edge("stat_agent", END)
    return graph.compile()
