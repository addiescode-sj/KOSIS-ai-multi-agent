"""Request and response bodies of the chat endpoints (docs/service/api.md 2, 8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from kosis_agent.graph.state import ChatResult

QUESTION_MAX = 200  # F-OMBGQM


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=QUESTION_MAX, description="사용자 질문 (한국어, 200자까지)",
                          examples=["2023년 시도별 출생아 수"])
    thread_id: str | None = Field(default=None, description="대화 ID. 같은 값을 보내면 이전 턴을 이어서 답한다. "
                                  "없으면 새 대화로 처리하고, 만든 ID를 `meta`로 돌려준다.", examples=["c0ffee"])
    retry_of: str | None = Field(default=None, description="\"다시 시도\"로 보낼 때 원 요청의 request_id (F-ZTUGMI)")
    # T3-48 (KOS-170) put the session in the body; the contract sends it as the X-Session-Id header (both accepted)
    session_id: uuid.UUID | None = Field(default=None, description="옛 방식: 브라우저 세션 UUID. `X-Session-Id` 헤더를 쓴다")
    input_method: Literal["direct", "clarify_option"] = Field(
        default="direct", description="direct: 직접 입력 · clarify_option: 되묻기 선택지 버튼. 로그용")


class ChatResponse(BaseModel):
    thread_id: str = Field(description="이 턴의 대화 ID (다음 턴에 그대로 보낸다)")
    trace_id: str = Field(description="이 요청의 OpenTelemetry trace ID = request_id (32자리 hex, Jaeger 검색)")
    expired: bool = Field(default=False, description="보낸 대화가 1시간 지나 만료돼 새 대화로 답했으면 true")
    result: ChatResult


class ConversationItem(BaseModel):
    id: uuid.UUID
    title: str = Field(description="첫 질문 앞부분")
    created_at: datetime
    updated_at: datetime = Field(description="마지막으로 물은 시각 (최근 순 정렬 기준)")
    questions: int = Field(description="질문 수")


class ConversationList(BaseModel):
    items: list[ConversationItem]
    next_cursor: str | None = Field(description="다음 쪽이 있으면 `cursor`로 보낼 값")


class TurnFeedback(BaseModel):
    value: Literal["up", "down"]
    category: str | None = Field(description="선택지 코드 (docs/service/api.md 3). 고르지 않고 닫았으면 null")


class ConversationTurn(BaseModel):
    turn: int
    question: str
    result: dict[str, Any] = Field(description="그때의 `ChatResult` (답·차트·출처)")
    created_at: datetime
    feedback: TurnFeedback | None = Field(default=None, description="이 답에 내가 남긴 마지막 피드백. 없으면 null")


class ConversationDetail(BaseModel):
    id: uuid.UUID
    title: str
    turns: list[ConversationTurn]


class ThreadMessage(BaseModel):
    role: str = Field(description="user | assistant")
    content: str


class ThreadState(BaseModel):
    thread_id: str
    turns: int = Field(description="사용자 질문 수")
    active_table: str | None = Field(description="대화가 지금 보고 있는 표 키 (다음 턴의 \"그 표\")")
    messages: list[ThreadMessage]
