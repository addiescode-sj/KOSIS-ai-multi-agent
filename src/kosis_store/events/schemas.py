"""Bodies of POST /events (docs/service/api.md 5). The events themselves: docs/log-schema.md 4."""

from __future__ import annotations

from pydantic import BaseModel, Field

from kosis_store.log_collector import MAX_EVENTS_PER_BATCH


class EventsRequest(BaseModel):
    events: list[dict] = Field(max_length=MAX_EVENTS_PER_BATCH,
                               description=f"화면 이벤트 목록(최대 {MAX_EVENTS_PER_BATCH}개). 형식은 docs/log-schema.md 4장")


class EventsResponse(BaseModel):
    accepted: int = Field(description="검사를 통과해 저장 대기열에 들어간 이벤트 수 (같은 event_id 중복은 1건)")
    rejected: int = Field(description="검사에서 떨어진 수 (사유별 건수만 남고 내용은 저장하지 않음)")
