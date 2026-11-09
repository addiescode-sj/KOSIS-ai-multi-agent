"""메인 그래프의 상태(AgentState)와 /chat/stream의 `result` 이벤트(ChatResult) (T3-23: graph.py에서 분리).

AgentState는 checkpointer가 thread_id별로 턴 사이에 이어 준다. 그래서 다음 턴에서 직전 턴의 검색·통계 결과
(= "그 표")를 읽을 수 있다. 큰 원자료(Parquet, 실전 1·2 내부 상태)는 여기에 두지 않는다. 실전 1·2 내부 상태는
runtime.without_checkpointer가 저장하지 않게 막는다.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import MessagesState
from pydantic import BaseModel, Field

from kosis_agent.services.chart import to_vega_lite
from kosis_ai.schemas import StatAnswer
from kosis_chat.schemas.io import StatOutput
from kosis_rag.schemas.io import SearchOutput, TableCandidate


class AgentState(MessagesState):
    """대화(messages) + 이번 턴의 검색 결과(search)와 통계 결과(stat).

    search: 실전 2 SearchOutput. 검색이 거절되면 stat은 None이다.
    stat: 실전 1 StatOutput (StatAnswer + ChartSpec + spans).
    """

    search: SearchOutput | None
    stat: StatOutput | None


# 체크포인트에서 다시 읽어도 되는 타입 목록. AgentState에 넣는 Pydantic 모델을 여기에 적는다.
# LangGraph checkpoint 직렬화기는 목록에 없는 타입을 읽을 때 경고를 내고, 이후 버전에서는 막는다
# (LANGGRAPH_STRICT_MSGPACK). 안쪽 모델(TableCandidate, StatAnswer 등)은 바깥 모델이 다시 만들므로 적지 않아도 된다.
# AgentState에 새 모델 필드를 추가하면 이 목록에도 추가해야 한다(tests/test_agent_graph.py가 경고 여부로 확인).
CHECKPOINT_TYPES = (SearchOutput, StatOutput)


def new_checkpointer() -> InMemorySaver:
    """대화를 thread_id별로 잇는 메모리 checkpointer. 위 CHECKPOINT_TYPES만 다시 읽도록 허용한다."""
    return InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=list(CHECKPOINT_TYPES)))


class ChatResult(BaseModel):
    """/chat/stream의 `result` 이벤트: 한 턴의 답, 표, 차트.

    앞쪽 필드(question, candidates, picked, no_answer, no_answer_reason, answer, answer_origin)는 프런트가
    실전 2의 옛 SearchResult에서 읽던 이름을 그대로 쓴다. 그래서 T3-00 UI가 바꾸지 않고 동작한다.
    """

    question: str
    route: str  # "refuse" (실전 2 gate가 거절) | "found"
    candidates: list[TableCandidate] = Field(default_factory=list)
    picked: list[TableCandidate] = Field(default_factory=list)
    no_answer: bool  # 검색 거절, 또는 실전 1이 refused / clarify로 답함
    no_answer_reason: str | None = None  # 검색 refusal_reason, 또는 실전 1 refusal.reason
    answer: str
    answer_origin: str  # "refusal" (실전 2 거절 문장) | "stat" (실전 1 StatAnswer.text)
    table_key: str | None = None  # 실전 1에 넘긴 tables.csv 키 ("<org_id>_<tbl_id>", tbl_id 아님)
    stat_answer: StatAnswer | None = None
    chart: dict[str, Any] | None = None  # Vega-Lite v5, 차트가 없으면 None


def chat_result(state: dict) -> ChatResult:
    """마지막 AgentState → ChatResult. 답 문장은 실전 1·2가 만든 것을 그대로 옮긴다(여기서 새로 쓰지 않음)."""
    search: SearchOutput = state["search"]
    stat: StatOutput | None = state.get("stat")
    base = {"question": search.question, "route": search.route, "candidates": search.candidates,
            "picked": search.picked}
    if stat is None:  # 검색 단계에서 거절됨 → 실전 2의 거절 문장(코드 텍스트)으로 끝
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
        chart=to_vega_lite(stat.chart),
    )
