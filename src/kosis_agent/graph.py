"""Multi-agent graph skeleton (T3-00): supervisor -> search_agent, over the phase 2 search graph as it is.

search_agent runs phase 2's compiled graph (parse -> retrieve -> [rerank1] -> [rerank2] -> gate -> refuse | guide)
unchanged, so its refusal gate, citation check and node trace come along. stat_agent (summarize_stat, T3-01) and
chart_agent (make_chart, T3-02) join the supervisor's routes later; T3-07~09 compare ReAct / Plan-and-Execute /
multi-agent on the same search graph.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph

from kosis_rag.graph import stream_search
from kosis_rag.schemas import SearchResult


class AgentState(MessagesState):
    """The conversation plus the last search result (tables and citations the next turns refer to, T3-04)."""

    search: SearchResult | None


def build_graph(search_graph):
    """Compile START -> supervisor -> search_agent -> END.

    In: search_graph = kosis_rag.graph.build_graph(resources, client, k=...) (a fake one in tests).
    Out: a compiled graph over AgentState. Streamed with stream_mode "custom", each phase 2 node arrives as
        (node name, its trace entry) and each checked guide piece as ("token", {"delta": ...}).
    """

    def supervisor(state: AgentState) -> dict:
        return {}

    def route(state: AgentState) -> str:
        return "search_agent"  # ponytail: one agent so far; the supervisor picks among agents from T3-03 / T3-09

    def search_agent(state: AgentState) -> dict:
        try:
            writer = get_stream_writer()
        except RuntimeError:  # invoked outside a stream
            writer = None
        final = None
        for name, payload in stream_search(search_graph, state["messages"][-1].content):
            if name == "result":
                final = payload
            elif writer:
                writer((name, payload))
        return {"messages": [AIMessage(final.result.answer)], "search": final.result}

    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor)
    graph.add_node("search_agent", search_agent)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", route, ["search_agent"])
    graph.add_edge("search_agent", END)
    return graph.compile()
