"""Request and response bodies of POST /feedback (docs/service/api.md 3)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class FeedbackRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=32, description="답의 request_id (`meta.trace_id`)")
    value: Literal["up", "down"]
    category: str | None = Field(default=None, description="선택지 코드 (docs/service/api.md 3). 고르지 않고 닫으면 null")
    reason: str | None = Field(default=None, max_length=200, description="이유 0~200자. category=other면 필수")


class FeedbackResponse(BaseModel):
    feedback_id: uuid.UUID
    updated: bool = Field(description="같은 답에 이미 남긴 피드백을 바꿨으면 true")
