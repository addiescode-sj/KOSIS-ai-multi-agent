"""메인 오케스트레이터 (코드): 에이전트 다섯을 어떤 순서로 부를지 조건부 경로로 정한다.

    START → judge(①) ─(차트만 다시)──────────────────────────────────────────┐
                  ├─(직전 표 재사용: 지시어·이월)→ feasibility ─┐               │
                  └─(새 질문 · 멀티쿼리)→ retrieve(②) → feasibility(③)        │
                                     feasibility ─(넘길 표 없음)→ finalize     │
                                                 └→ calculate(④) ─(답함)→ chart(⑤) → finalize
                                                      ├─(이월 턴, 직전 표가 표 불일치로 거절)→ retrieve(다시 쓴 문장)
                                                      ├─(표 불일치 거절 · 예산 남음)→ feasibility(다음 후보)
                                                      └─(거절 · 되묻기 · 멀티쿼리 답)→ finalize
    finalize → END   (outcome: answered | refused | clarify | failed)

뼈대 단계: 경로와 결과는 기존 kosis_agent 그래프와 같게 둔다(tests/multi 패리티 테스트).
TODO(S2): judge의 route가 direct | clarify면 retrieve로 가지 않고 finalize로.
TODO(결정 1): 메인을 코드로 둘지 확정. LLM 감독자 변형은 보류(결정 8).
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from kosis_agent.graph.context import LLMReader, Mode
from kosis_agent.graph.main_graph import NO_TABLE_TEXT, TABLE_MISMATCH, default_parser
from kosis_agent.graph.multiquery import SplitReader
from kosis_agent.services.chart import CHART_NAMES
from kosis_agent.services.combine import combine_text
from kosis_chat.api import summarize_stat_run
from kosis_chat.schemas.io import StatOutput
from kosis_chat.schemas.resources import LLMConfig as StatLLMConfig
from kosis_multi.agents.calculator import make_calculator
from kosis_multi.agents.chart import make_chart_agent
from kosis_multi.agents.feasibility import make_feasibility, next_key
from kosis_multi.agents.judge import make_judge
from kosis_multi.agents.retriever import make_retriever
from kosis_multi.state import MultiState
from kosis_multi.tools import make_toolbox
from kosis_rag.parse.schemas import ParsedQuery
from kosis_rag.schemas.io import SearchOutput


def timed(name: str, fn: Callable) -> Callable:
    """에이전트 하나를 노드로: 걸린 시간을 timings[name]에 더한다(같은 턴에 여러 번 돌면 합)."""
    wants_config = "config" in inspect.signature(fn).parameters

    def node(state: dict, config: RunnableConfig) -> dict:
        started = time.perf_counter()
        update = fn(state, config) if wants_config else fn(state)
        timings = dict(update.get("timings", state.get("timings") or {}))
        timings[name] = timings.get(name, 0.0) + (time.perf_counter() - started) * 1000
        return {**update, "timings": timings}

    return node


def finalize(state: dict) -> dict:
    """outcome과 답 메시지. 답 문장은 실전 1·2가 만든 것이나 코드 고정 문장만 쓴다(LLM 문장 생성 없음)."""
    stat: StatOutput | None = state.get("stat")
    if (results := state.get("sub_results")) is not None:  # 멀티쿼리: 하위 답을 순서대로 잇는다
        parts = [r["text"] for r in results]
        if (combined := state.get("combined")) is not None:
            parts.append(combine_text(combined))
        answered = [r for r in results if r["outcome"] == "answered"]
        update = {"outcome": "answered" if answered else results[0]["outcome"],
                  "messages": [AIMessage("\n\n".join(parts))]}
        if stat is not None:
            update.update(active_table=state["table_key"], active_question=state["resolved_question"],
                          active_chart=stat.chart)
        return update
    if state["chart_only"]:  # 차트만 다시: 대화 상태는 그대로
        if state.get("chart") is None:
            return {"outcome": "failed", "messages": [AIMessage(state.get("chart_note") or "")]}
        name = CHART_NAMES.get(state.get("chart_type") or "", "차트")
        text = f"직전 답을 {name}로 다시 그렸어요." + (f" {state['chart_note']}" if state.get("chart_note") else "")
        return {"outcome": "answered", "messages": [AIMessage(text)]}
    if stat is None:  # ④까지 가지 못함: 검색 거절, 또는 넘길 표 없음
        out: SearchOutput = state["search"]
        if out.route == "refuse":
            return {"outcome": "refused", "messages": [AIMessage(out.refusal_text or "")]}
        return {"outcome": "failed", "messages": [AIMessage(NO_TABLE_TEXT)]}
    return {"outcome": stat.answer.outcome, "active_table": state["table_key"],
            "active_question": state.get("resolved_question") or state["question"],
            "active_chart": stat.chart, "messages": [AIMessage(stat.answer.text)]}


def build_graph(search: Any, stat_llm: StatLLMConfig, *, retry_interval: float = 0.0,
                summarize: Callable[..., StatOutput] = summarize_stat_run, checkpointer: Any = None,
                alt_table_budget: int = 0, parse: Callable[[str], ParsedQuery] | None = None,
                interpreter: Mode = "hybrid", llm: LLMReader | None = None, splitter: SplitReader | None = None):
    """멀티에이전트 그래프를 compile한다. 인자는 기존 kosis_agent.graph.main_graph.build_graph와 같다(패리티 테스트용).

    TODO(S2): judge_llm(Qwen) 인자 추가. TODO(S4): max_concurrency·prefetch 인자 추가.
    """
    if alt_table_budget < 0:
        raise ValueError("alt_table_budget cannot be negative")
    parse = parse or default_parser(search)
    tools = make_toolbox(search, stat_llm, retry_interval=retry_interval, summarize=summarize)

    def after_judge(state: dict) -> str:
        if state["chart_only"]:
            return "chart"
        if state["reused_table"] and not state.get("sub_questions"):
            return "feasibility"
        return "retrieve"

    def after_feasibility(state: dict) -> str:
        if state.get("sub_keys") is not None:  # 멀티쿼리: 표가 없는 하위 질문도 ④가 거절 문장을 붙인다
            return "calculate"
        return "calculate" if state.get("plan_key") is not None else "finalize"

    def after_calculate(state: dict) -> str:
        if state.get("sub_results") is not None:  # 멀티쿼리 답에는 차트를 그리지 않는다(기존과 같음)
            return "finalize"
        last = state["attempts"][-1]
        if last["outcome"] == "answered":
            return "chart"
        if state.get("sub_searches") is not None:  # 멀티쿼리를 같은 표로 되돌린 턴: 재시도·다시 검색 없음
            return "finalize"
        mismatch = last["outcome"] == "refused" and last["reason"] in TABLE_MISMATCH
        if mismatch and state.get("carried_over") and not state.get("fallback_searched"):
            return "retrieve"
        if mismatch and len(state["attempts"]) <= alt_table_budget and next_key(state) is not None:
            return "feasibility"
        return "finalize"

    graph = StateGraph(MultiState)
    graph.add_node("judge", timed("judge", make_judge(parse, interpreter=interpreter, llm=llm, splitter=splitter)))
    graph.add_node("retrieve", timed("retriever", make_retriever(tools)))
    graph.add_node("feasibility", timed("feasibility", make_feasibility()))
    graph.add_node("calculate", timed("calculator", make_calculator(tools, parse)))
    graph.add_node("chart", timed("chart", make_chart_agent(tools)))
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "judge")
    graph.add_conditional_edges("judge", after_judge, ["chart", "feasibility", "retrieve"])
    graph.add_edge("retrieve", "feasibility")
    graph.add_conditional_edges("feasibility", after_feasibility, ["calculate", "finalize"])
    graph.add_conditional_edges("calculate", after_calculate, ["chart", "retrieve", "feasibility", "finalize"])
    graph.add_edge("chart", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
