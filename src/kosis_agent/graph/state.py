"""메인 그래프의 상태(AgentState)와 /chat/stream의 `result` 이벤트(ChatResult) (T3-23: graph.py에서 분리).

AgentState는 checkpointer가 thread_id별로 턴 사이에 이어 준다. 상태는 두 종류로 나뉜다.
- 대화 상태 (턴을 넘어 남음): messages, active_table(대화가 지금 보고 있는 표 = 다음 턴의 "그 표"), search(마지막 검색)
- 이번 턴 상태 (context 노드가 턴마다 비움): question, reused_table, attempts, stat, table_key, chart, outcome, degraded

큰 원자료(Parquet, 실전 1·2 내부 상태)는 여기에 두지 않는다. 실전 1·2 내부 상태는 runtime.without_checkpointer가
저장하지 않게 막는다.
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import MessagesState
from pydantic import BaseModel, Field

from kosis_ai.schemas import ChartSpec, StatAnswer
from kosis_chat.schemas.io import StatOutput
from kosis_rag.schemas.io import SearchOutput, TableCandidate

# 한 턴이 끝나는 방식 4가지 (plans/langgraph-statistics-rag-architecture.md 9절의 종료 조건을 이 레포에 맞춘 것)
#   answered : 실전 1이 숫자로 답함
#   refused  : 검색 거절, 또는 실전 1이 표 불일치·질문 문제로 거절 (이유는 ChatResult.no_answer_reason)
#   clarify  : 실전 1이 되물음 (예: "어느 지역 기준인가요?")
#   failed   : 검색은 found인데 넘길 표가 하나도 없음 (실전 2 계약상 드묾)
Outcome = Literal["answered", "refused", "clarify", "failed"]


class AgentState(MessagesState):
    """메인 그래프 상태. 필드 설명은 모듈 docstring의 두 분류를 따른다."""

    # 대화 상태
    active_table: str | None  # 대화가 보고 있는 표 키. 다음 턴 질문에 "그 표" 같은 지시어가 있으면 이 표를 다시 쓴다
    active_question: str | None  # active_table로 마지막에 물은 독립 질문. "부산은?" 같은 이월은 이 문장에 조건을 바꿔 끼운다 (T3-27)
    active_chart: ChartSpec | None  # 마지막 답의 차트 데이터. "그 표로 막대그래프"는 이것을 다시 그린다 (T3-27)
    search: SearchOutput | None  # 마지막 검색 결과. 지시어로 표를 다시 쓴 턴에는 직전 턴 것이 그대로 남는다
    # 이번 턴 상태
    question: str  # 이번 턴 사용자 질문 (원문)
    resolved_question: str  # 실전 1에 넘긴 문장: 이월이면 직전 조건을 채운 독립 질문, 아니면 원문 (T3-27)
    interpreted_by: str  # 이번 턴을 누가 해석했나: rule | llm | none (graph/context.py Reading.by)
    chart_requested: bool  # 사용자가 이번 턴에 차트를 요청함 → make_chart 툴을 부른다 (T3-27)
    chart_type: str | None  # 요청한 종류 bar | line | pie (없으면 실전 1이 고른 종류)
    chart_only: bool  # 숫자는 그대로 두고 직전 답을 차트로만 다시 그림
    chart_note: str | None  # 요청한 종류로 못 그렸거나 차트 데이터가 없을 때의 안내
    reused_table: bool  # True면 이번 턴은 검색을 건너뛰고 active_table로 답함
    attempts: list[dict]  # 실전 1에 넘긴 표마다 {"key", "outcome", "reason"}, 시도 순서대로
    stat: StatOutput | None  # 이번 턴의 최종 실전 1 결과 = 마지막으로 시도한 표의 결과
    table_key: str | None  # stat을 만든 표 키 (tables.csv 키 "<org_id>_<tbl_id>", tbl_id 아님)
    chart: dict | None  # stat.chart를 Vega-Lite로 바꾼 것
    outcome: Outcome | None
    degraded: bool  # 2차 리랭커가 실패해 실전 2가 1차 순서로 대신 답함 (plans/agent-graph-refactor.md P4)


# 체크포인트에서 다시 읽어도 되는 타입 목록. AgentState에 넣는 Pydantic 모델을 여기에 적는다.
# LangGraph checkpoint 직렬화기는 목록에 없는 타입을 읽을 때 경고를 내고, 이후 버전에서는 막는다
# (LANGGRAPH_STRICT_MSGPACK). 안쪽 모델(TableCandidate, StatAnswer 등)은 바깥 모델이 다시 만들므로 적지 않아도 된다.
# AgentState에 새 모델 필드를 추가하면 이 목록에도 추가해야 한다(tests/test_agent_graph.py가 경고 여부로 확인).
CHECKPOINT_TYPES = (SearchOutput, StatOutput, ChartSpec)


def new_checkpointer() -> InMemorySaver:
    """대화를 thread_id별로 잇는 메모리 checkpointer. 위 CHECKPOINT_TYPES만 다시 읽도록 허용한다."""
    return InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=list(CHECKPOINT_TYPES)))


class ChatResult(BaseModel):
    """/chat/stream의 `result` 이벤트: 한 턴의 답, 표, 차트.

    앞쪽 필드(question, candidates, picked, no_answer, no_answer_reason, answer, answer_origin)는 프런트가
    실전 2의 옛 SearchResult에서 읽던 이름을 그대로 쓴다. 그래서 T3-00 UI가 바꾸지 않고 동작한다.
    outcome · attempted_keys · reused_table · degraded는 T3-23, resolved_question · interpreted_by · chart_type ·
    chart_note는 T3-27에서 추가한 선택 필드다(옛 프런트는 무시함).
    """

    question: str
    route: str  # "refuse" (실전 2 gate가 거절) | "found"
    candidates: list[TableCandidate] = Field(default_factory=list)
    picked: list[TableCandidate] = Field(default_factory=list)
    no_answer: bool  # outcome이 answered가 아니면 True
    no_answer_reason: str | None = None  # 검색 refusal_reason, 또는 실전 1 refusal.reason
    answer: str
    answer_origin: str  # "refusal" (코드 거절 문장: 실전 2 gate 또는 failed) | "stat" (실전 1 StatAnswer.text) | "chart" (차트만 다시 그린 턴의 코드 문장)
    table_key: str | None = None  # 답을 만든 tables.csv 키 ("<org_id>_<tbl_id>", tbl_id 아님)
    stat_answer: StatAnswer | None = None
    chart: dict[str, Any] | None = None  # Vega-Lite v5, 차트가 없으면 None
    outcome: str | None = None  # answered | refused | clarify | failed
    attempted_keys: list[str] = Field(default_factory=list)  # 실전 1에 넘긴 표 키, 시도 순서대로 (대체 표 재시도 포함)
    reused_table: bool = False  # 지시어로 직전 표를 다시 씀 (검색 건너뜀)
    degraded: bool = False  # 2차 리랭커 실패 → 실전 2가 1차 순서로 대신 답함
    resolved_question: str | None = None  # 실전 1에 넘긴 문장 (이월이면 조건을 채운 독립 질문)
    interpreted_by: str | None = None  # rule | llm | none
    chart_type: str | None = None  # 사용자가 요청한 차트 종류
    chart_note: str | None = None  # 요청대로 못 그렸을 때의 안내


def chat_result(state: dict) -> ChatResult:
    """마지막 AgentState → ChatResult. 답 문장은 실전 1·2가 만든 것(또는 코드 고정 문장)을 그대로 옮긴다."""
    search: SearchOutput = state["search"]
    stat: StatOutput | None = state.get("stat")
    base = {"question": state.get("question") or search.question, "route": search.route,
            "candidates": search.candidates, "picked": search.picked, "outcome": state.get("outcome"),
            "attempted_keys": [a["key"] for a in state.get("attempts") or []],
            "reused_table": bool(state.get("reused_table")), "degraded": bool(state.get("degraded")),
            "resolved_question": state.get("resolved_question"), "interpreted_by": state.get("interpreted_by"),
            "chart_type": state.get("chart_type"), "chart_note": state.get("chart_note")}
    if state.get("chart_only"):  # 차트만 다시 그린 턴: 숫자는 직전 답 그대로, 답 문장은 코드 고정 문장
        return ChatResult(**base, no_answer=state.get("outcome") != "answered", answer=state["messages"][-1].content,
                          answer_origin="chart", table_key=state.get("active_table"), chart=state.get("chart"))
    if stat is None:  # 실전 1까지 가지 못함 → 마지막 AI 메시지(코드 거절 문장)가 답
        return ChatResult(**base, no_answer=True, no_answer_reason=search.refusal_reason,
                          answer=state["messages"][-1].content, answer_origin="refusal")
    answer = stat.answer
    return ChatResult(
        **base,
        no_answer=answer.outcome != "answered",
        no_answer_reason=answer.refusal.reason if answer.refusal else None,
        answer=answer.text,
        answer_origin="stat",
        table_key=state.get("table_key"),
        stat_answer=answer,
        chart=state.get("chart"),
    )
