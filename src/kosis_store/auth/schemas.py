"""Bodies of /auth/* (docs/service/api.md 6)."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Color = Literal["blue", "green", "amber", "rose", "slate"]
RetentionDays = Literal[90, 30]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20,
                                               pattern=r"^[^\x00-\x1f\x7f]+$")]


class MeResponse(BaseModel):
    user_id: uuid.UUID
    nickname: str = Field(description="카카오 닉네임")
    display_name: str | None = Field(description="화면에 보일 이름. null이면 카카오 닉네임")
    color: Color = Field(description="프로필 색")
    save_history: bool = Field(description="새 대화를 계정에 저장할지")
    retention_days: RetentionDays = Field(description="마지막 질문부터 대화를 보관하는 날 수")


class MeUpdate(BaseModel):
    """PATCH /auth/me: only the fields sent are changed. Only display_name may be null (back to the Kakao nickname)."""

    model_config = ConfigDict(extra="forbid")

    display_name: DisplayName | None = None
    color: Color = "blue"
    save_history: bool = True
    retention_days: RetentionDays = 90
