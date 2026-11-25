"""멀티에이전트 툴 래핑 (새로 정의). 안쪽 함수(실전 1·2, 차트·합산 계산)는 기존 것을 그대로 부른다.

채점 계약은 기존 툴과 같게 둔다 (evals/evaluators/agent/tools.py가 `execute_tool <name>` span의 이름·인자·결과로 채점):
- 이름: search_tables · summarize_stat · combine_values · make_chart
- 인자 이름·content 형식도 같다. 기대 목록에 없는 툴은 extra_call 감점이라, ③ 답변 가능성·즉시 답변은 툴로 만들지 않는다.

기존과 다른 점
- 툴마다 주인 에이전트를 metadata["agent"]에 적는다(트레이스에서 누가 불렀는지 보임).
- search_tables는 ③이 닫는다(S3): ②가 찾아 둔 후보(search_steps 보관함)에 rerank2 → gate만 돈다.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langchain_core.messages import ToolMessage

from kosis_agent.observability.tracing import STAGE2_MODEL
from kosis_agent.runtime import stream_writer, without_checkpointer
from kosis_agent.services.chart import draw
from kosis_agent.services.combine import Op, combine
from kosis_agent.services.statistics import not_in_catalog
from kosis_ai.schemas import ChartSpec, StatAnswer
from kosis_chat.api import summarize_stat_run
from kosis_chat.schemas.io import StatOutput
from kosis_chat.schemas.resources import LLMConfig as StatLLMConfig
from kosis_multi.search_steps import SearchSteps
from kosis_rag.schemas.io import SearchOutput


def call_tool(tool_: BaseTool, args: dict, config: RunnableConfig) -> ToolMessage:
    """ToolCall 형태로 부른다(artifact를 받으려면 id가 있어야 함). 노드 config를 넘겨야 툴 span이 이 턴 아래에 열린다."""
    call = {"name": tool_.name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}
    return tool_.invoke(call, config)


def _owned(tool_: BaseTool, agent: str, **extra: Any) -> BaseTool:
    tool_.metadata = {**(tool_.metadata or {}), "agent": agent, **extra}
    return tool_


def search_tables_tool(steps: SearchSteps) -> BaseTool:
    """③ 답변 가능성 에이전트의 툴(S3): ②가 찾아 둔 후보에 2차 리랭커(Qwen) → gate를 돌려 SearchOutput을 남긴다.

    채점기가 이 툴 결과의 route로 검색 거절을 읽으므로, 리랭커 거절까지 끝난 최종 결과를 여기서 닫는다.
    ②의 후보가 없으면(② 없이 부른 경우) 처음부터 다 돈다. 인자·결과 형식은 기존 search_tables와 같다.
    """

    @tool("search_tables", response_format="content_and_artifact")
    def search_tables(question: str, config: RunnableConfig) -> tuple[dict, SearchOutput]:
        """KOSIS 통계표를 찾는다. route(found | refuse), 고른 표(picked: key·표 이름·기간·출처), 후보(candidates)를 돌려준다."""
        out = steps.rerank_and_gate(question, config)
        return out.model_dump(mode="json"), out

    stage2 = getattr(getattr(steps.search, "resources", None), "stage2_llm", None)
    extra = {STAGE2_MODEL: stage2.model} if getattr(stage2, "model", None) else {}
    return _owned(search_tables, "feasibility", **extra)


def summarize_stat_tool(stat_llm: StatLLMConfig, *, retry_interval: float = 0.0,
                        summarize: Callable[..., StatOutput] = summarize_stat_run) -> BaseTool:
    """④ 계산 에이전트의 툴: 실전 1 summarize_stat_run 한 번. 숫자는 실전 1 코드가 계산한다."""

    @tool("summarize_stat", response_format="content_and_artifact")
    def summarize_stat(question: str, table_key: str, period_start: str | None = None, period_end: str | None = None,
                       regions: list[str] | None = None, dimensions: list[str] | None = None,
                       rewritten: str | None = None) -> tuple[dict, StatOutput]:
        """search_tables가 찾은 표 하나(table_key = picked의 key, 예: 101_DT_1B8000H)로 질문에 답한다.
        숫자는 코드가 원자료에서 계산한다. 답(answered), 거절(refused, 이유 포함), 되묻기(clarify) 중 하나를 돌려준다.
        period_start·period_end는 질문의 기간("YYYY" 또는 "YYYYMM"), regions는 시도 공식 이름(예: "서울특별시"),
        dimensions는 분류(예: "성", "연령")다. 질문에 없으면 비워 둔다.
        rewritten은 계산용으로 다듬은 질문(작년 → 연도, 오타, 구어 지표)이다. 있으면 그것으로 답한다."""
        del period_start, period_end, regions, dimensions  # 기록·채점용 (실전 1은 question에서 조건을 읽음)
        started = time.perf_counter()
        try:
            with without_checkpointer():
                stat = summarize(rewritten or question, table_key, llm=stat_llm, retry_interval=retry_interval)
        except LookupError as error:
            stat = not_in_catalog(error)
        writer = stream_writer()
        if writer:
            writer(("stat", {"seconds": time.perf_counter() - started, "key": table_key, "spans": stat.spans}))
        return stat.answer.model_dump(mode="json"), stat

    return _owned(summarize_stat, "calculator")


def combine_values_tool() -> BaseTool:
    """④ 계산 에이전트의 툴: 표가 다른 두 하위 답의 숫자를 코드로 합친다."""

    @tool("combine_values", response_format="content_and_artifact")
    def combine_values(op: Op, answers: list[dict]) -> tuple[dict, dict]:
        """서로 다른 표에서 받은 summarize_stat 답 두 개(answers, 질문 순서)의 숫자를 코드로 합친다.
        op: diff(앞 − 뒤) | sum(합) | ratio(앞 ÷ 뒤 × 100, %). 숫자가 하나가 아니거나 단위가 다르면 계산하지 않는다."""
        result = combine(op, [StatAnswer.model_validate(a) for a in answers])
        return result, result

    return _owned(combine_values, "calculator")


def make_chart_tool() -> BaseTool:
    """⑤ 차트 에이전트의 툴: 사용자가 차트를 요청한 턴에만 부른다(요청 안 한 턴은 chart_payload 함수).
    종류 결정은 실전 1 차트 모듈(T1-32, services.chart.draw → redraw)이 한다. Vega-Lite는 없다(T3-02, KOS-202)."""

    @tool("make_chart", response_format="content_and_artifact")
    def make_chart(table_key: str, chart: dict | None = None, chart_type: str | None = None) -> tuple[dict, dict]:
        """summarize_stat이 돌려준 차트 데이터(ChartSpec)를 요청한 종류로 다시 그린다.
        table_key는 그 차트를 만든 표의 key, chart_type은 사용자가 요청한 종류(bar | line | pie | stacked, 없으면 그 차트 종류)다."""
        out = draw(ChartSpec.model_validate(chart) if chart else None, chart_type)
        return {"table_key": table_key, "chart_type": chart_type, "kind": out["kind"],
                "drawn": out["chart"] is not None, "note": out["note"]}, out

    return _owned(make_chart, "chart")


@dataclass(frozen=True)
class Toolbox:
    """에이전트별 툴. orchestrator가 한 번 만들어 각 에이전트에 넘긴다."""

    search_tables: BaseTool
    summarize_stat: BaseTool
    combine_values: BaseTool
    make_chart: BaseTool


def make_toolbox(steps: SearchSteps, stat_llm: StatLLMConfig, *, retry_interval: float = 0.0,
                 summarize: Callable[..., StatOutput] = summarize_stat_run) -> Toolbox:
    return Toolbox(search_tables=search_tables_tool(steps),
                   summarize_stat=summarize_stat_tool(stat_llm, retry_interval=retry_interval, summarize=summarize),
                   combine_values=combine_values_tool(), make_chart=make_chart_tool())
