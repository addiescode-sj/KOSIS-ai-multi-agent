"""Bodies shared by the API's domains. The rest is per domain: chat/, feedback/, events/, tools/ (schemas.py in each).

Only the API imports the schemas: the migrate image has kosis_store without the agent code that chat/ and tools/ use,
so kosis_store/__init__.py, the table models and migrations must not import them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str = Field(examples=["not_found"])
    message: str = Field(description="화면에 보일 수 있는 문장")


class ErrorResponse(BaseModel):
    """SSE가 아닌 엔드포인트의 오류 (docs/service/api.md 0)."""

    error: ErrorDetail
