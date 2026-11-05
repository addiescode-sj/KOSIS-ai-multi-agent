"""Agent graph skeleton (T3-00): one LLM node on MessagesState.

T3-01 binds search_tables / summarize_stat / make_chart as tools, T3-03 turns this into the ReAct loop,
T3-08 / T3-09 build the Plan-and-Execute and multi-agent variants on the same state.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, MessagesState, StateGraph


def build_graph(llm: BaseChatModel):
    """Compile START -> agent -> END. The agent node appends the LLM reply to the conversation."""

    def agent(state: MessagesState) -> dict:
        return {"messages": [llm.invoke(state["messages"])]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    return graph.compile()
