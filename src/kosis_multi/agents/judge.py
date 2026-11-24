"""① 판단 에이전트: 이번 턴을 어떻게 처리할지 정한다.

정하는 것: 경로(route), 실전 1에 넘길 문장(이월이면 다시 쓴 독립 질문), 직전 표 재사용, 하위 질문, 차트 요청.
지금(뼈대)은 기존 해석 규칙(read_turn · split_question, 규칙이 못 정한 턴만 기존 LLM 단계)을 그대로 쓰고 route는 항상 rag다.

TODO(S2, 결정 대기): Qwen(2차 리랭커와 같은 서버)으로 rag | direct | clarify 판단. 규칙이 먼저, 규칙이 못 정한 턴만 Qwen.
TODO(결정 4): direct(즉시 답변) 범위 = 인사·사용법·서비스 범위만, 숫자가 든 답은 막는 안.
"""

from __future__ import annotations

from collections.abc import Callable

from kosis_agent.graph.context import LLMReader, Mode, chart_request, read_turn
from kosis_agent.graph.multiquery import SplitReader, split_question
from kosis_rag.parse.schemas import ParsedQuery


def make_judge(parse: Callable[[str], ParsedQuery], *, interpreter: Mode = "hybrid", llm: LLMReader | None = None,
               splitter: SplitReader | None = None):
    """상태 → 이번 턴 필드. 이번 턴 상태를 모두 비우는 것도 여기서 한다(턴의 첫 에이전트)."""

    def judge(state: dict) -> dict:
        question = state["messages"][-1].content
        reading = read_turn(question, active_table=state.get("active_table"),
                            active_question=state.get("active_question"), parse=parse, mode=interpreter, llm=llm)
        wants_chart = reading.chart_only or reading.chart_type is not None or chart_request(question)[0]
        carried = (reading.use_previous_table and not reading.chart_only
                   and reading.resolved_question != question)
        split = None if reading.use_previous_table else split_question(question, parse, splitter)
        subs = split.sub_questions if split and len(split.sub_questions) > 1 else []
        return {
            # ① 판단 결과
            "route": "rag", "question": question, "resolved_question": reading.resolved_question,
            "interpreted_by": reading.by, "reused_table": reading.use_previous_table, "carried_over": carried,
            "chart_requested": wants_chart, "chart_type": reading.chart_type, "chart_only": reading.chart_only,
            "sub_questions": subs, "combine_op": split.op if subs else None, "split_by": split.by if subs else None,
            # 이번 턴 상태 비우기 (②~⑤가 채움)
            "fallback_searched": False, "chart_note": None, "attempts": [], "stat": None, "table_key": None,
            "chart": None, "outcome": None, "degraded": False, "sub_results": None, "combined": None,
            "plan_key": None, "sub_searches": None, "sub_keys": None, "timings": {},
        }

    return judge
