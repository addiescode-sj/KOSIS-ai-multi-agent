"""Response bodies of /auth/* (docs/service/api.md 6)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class MeResponse(BaseModel):
    user_id: uuid.UUID
    nickname: str = Field(description="카카오 닉네임")
