"""Request and response bodies of the developer tool endpoints, /dev/tools/* (docs/service/api.md 8)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from kosis_ai.schemas import StatAnswer


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, description="통계표를 찾을 질문", examples=["2023년 시도별 출생아 수"])


class StatRequest(BaseModel):
    question: str = Field(min_length=1, description="표 하나로 답할 질문", examples=["2023년 출생아 수"])
    table_key: str = Field(min_length=1, description="tables.csv 키 `<org_id>_<tbl_id>` (search_tables의 picked[].key)",
                           examples=["101_DT_1B8000H"])


class StatResponse(BaseModel):
    answer: StatAnswer = Field(description="실전 1 답: answered | refused(이유 포함) | clarify")
    chart: dict | None = Field(description="실전 1 ChartSpec. `/dev/tools/make_chart`의 `chart`로 그대로 넘긴다")
    vega_lite: dict | None = Field(description="chart를 기본 종류로 그린 Vega-Lite spec")


class ChartRequest(BaseModel):
    table_key: str = Field(min_length=1, description="차트를 만든 표 키", examples=["101_DT_1B8000H"])
    chart: dict | None = Field(default=None, description="`/dev/tools/summarize_stat` 응답의 chart (ChartSpec)")
    chart_type: str | None = Field(default=None, description="bar | line | pie. 없으면 실전 1이 고른 종류",
                                   examples=["bar"])


class ChartResponse(BaseModel):
    vega_lite: dict | None = Field(description="Vega-Lite spec. 차트 데이터가 없으면 null")
    kind: str | None = Field(description="실제로 그린 종류")
    note: str | None = Field(description="요청한 종류로 못 그렸을 때의 안내")
