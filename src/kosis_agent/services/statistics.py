"""summarize_stat 툴: 실전 1 수치 요약 모듈(kosis_chat.api.summarize_stat_run)을 LangChain 툴로 감싼다 (T3-23).

숫자는 실전 1의 코드(pandas)가 계산하고, 답 문장도 실전 1이 숫자 검증(verify)을 통과시킨 StatAnswer.text를 쓴다.
이 툴은 숫자나 문장을 새로 만들지 않는다. 유일한 예외는 표 키가 tables.csv에 없을 때의 거절 문장이며, 이것도
코드에 고정된 문장이다(LLM 아님).

툴 계약 (evals/evaluators/agent.py가 읽는 형태):
- 인자: question, table_key (필수) + period_start, period_end, regions, dimensions (선택).
  - table_key: 하네스의 state_key()가 args["table_key"]로 "이 턴이 어떤 표에서 끝났는지"를 읽는다.
  - 선택 인자 4개: 이 질문을 어떤 조건(기간·시도·분류)으로 물었는지. 이름은 하네스 채점 필드(evals/schemas.ARG_FIELDS),
    L2 라벨(evals/schemas.QueryItem), 실전 2 질의 해석 결과(kosis_rag.parse.schemas.ParsedQuery)와 같다.
    그래서 실전 2 parse_query의 결과를 그대로 넣을 수 있고, 하네스가 L2와 같은 규칙(field_correct)으로 채점한다.
    실전 1은 조건을 질문 문장에서 직접 읽으므로 이 값을 실전 1에 넘기지 않는다(summarize_stat_run에 그런 인자가 없음).
    즉 "에이전트가 이 조건으로 물었다"는 기록이자 파라미터 정확도의 채점 대상이다.
- content: StatAnswer JSON. 하네스의 stat_answer()가 이것을 StatAnswer로 검증해 L3 채점기에 넣고, refused()가
  outcome == "refused"로 거절을 읽는다.
- artifact: StatOutput 객체(StatAnswer + ChartSpec + spans). 노드가 꺼내 차트와 span을 쓴다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from kosis_agent.runtime import stream_writer, without_checkpointer
from kosis_ai.schemas import Refusal, StatAnswer
from kosis_chat.api import summarize_stat_run, table_path
from kosis_chat.schemas.io import StatOutput
from kosis_chat.schemas.resources import LLMConfig as StatLLMConfig

# 표 키가 tables.csv에 없을 때 사용자에게 보여 줄 문장 (코드 고정 문장, LLM 아님)
NOT_IN_CATALOG_TEXT = "찾은 통계표의 원자료를 열 수 없어 답할 수 없어요. 다른 표현으로 다시 물어봐 주세요."
# 표가 너무 커서 읽지 않을 때 보여 줄 문장 (코드 고정 문장, LLM 아님)
TABLE_TOO_LARGE_TEXT = "찾은 통계표가 너무 커서 지금은 바로 계산할 수 없어요. 지역이나 기간을 좁혀 다시 물어봐 주세요."
# T3-23: 실전 1은 표 전체를 읽어 행마다 Python 객체를 만든다(행당 수 KB). 이보다 큰 표는 메모리를 다 써서 PC가 멈췄다
# (11/10 e2e). 골든 정답 표 중 가장 큰 표는 약 16만 행이고, 이 한도를 넘는 표는 54개다. 0 = 한도 없음.
MAX_TABLE_ROWS = 1_000_000


def table_rows(table_key: str) -> int | None:
    """표의 행 수 (Parquet footer만 읽음, 데이터는 안 읽음). tables.csv에 없거나 파일이 없으면 None
    (그 경우는 실전 1이 LookupError로 알려 주고 not_in_catalog 거절이 된다)."""
    import pyarrow.parquet as pq

    try:
        path: Path = table_path(table_key)
    except LookupError:
        return None
    return pq.ParquetFile(path).metadata.num_rows


def not_in_catalog(error: LookupError) -> StatOutput:
    """실전 1이 표를 열지 못했을 때(LookupError)의 거절 답.

    리팩토링 전에는 이 경우가 SSE `error` 이벤트로 끝났다. 이제는 거절(outcome "refused")로 바꿔서
    하네스가 거절로 채점하고, 대화도 끊기지 않게 한다(plans/T3-23.md "동작 변경 1건").
    reason은 실전 1의 RefusalReason 중 가장 가까운 out_of_scope(제공된 표 범위 밖)를 쓴다.
    """
    refusal = Refusal(reason="out_of_scope", message=NOT_IN_CATALOG_TEXT, available=None)
    answer = StatAnswer(outcome="refused", text=NOT_IN_CATALOG_TEXT, refusal=refusal)
    return StatOutput(answer=answer, chart=None, spans=[{"name": "table_lookup", "status": "error",
                                                         "error": f"{type(error).__name__}: {error}"}])


def too_large(table_key: str, rows: int, limit: int) -> StatOutput:
    """행 수가 한도를 넘어 실전 1을 부르지 않은 표의 거절 답 (not_in_catalog와 같은 모양, reason out_of_scope)."""
    refusal = Refusal(reason="out_of_scope", message=TABLE_TOO_LARGE_TEXT, available=None)
    answer = StatAnswer(outcome="refused", text=TABLE_TOO_LARGE_TEXT, refusal=refusal)
    return StatOutput(answer=answer, chart=None, spans=[{"name": "table_size", "status": "error",
                                                         "error": f"TableTooLarge: {table_key}: {rows:,} rows > {limit:,}"}])


def make_summarize_stat_tool(stat_llm: StatLLMConfig, *, retry_interval: float = 0.0,
                             summarize: Callable[..., StatOutput] = summarize_stat_run,
                             max_table_rows: int = MAX_TABLE_ROWS,
                             count_rows: Callable[[str], int | None] = table_rows) -> BaseTool:
    """실전 1을 감싼 summarize_stat 툴을 만든다.

    In: stat_llm = 실전 1의 LLMConfig (실전 2와 다른 클래스); retry_interval = HCX 429 때 두 번째 호출 전 대기 초;
        summarize = 실전 1 summarize_stat_run (테스트에서는 가짜);
        max_table_rows = 이보다 행이 많은 표는 실전 1을 부르지 않고 거절한다 (0 = 한도 없음, T3-23);
        count_rows = 표 키 → 행 수 (None = 모름, 검사 생략; 테스트에서는 가짜).
    Out: summarize_stat 툴. 실행이 끝나면 ("stat", {"seconds", "key", "spans"}) 이벤트를 stream writer로 내보낸다
        (리팩토링 전 stat_agent 노드가 내던 것과 같은 이름·내용).
    Errors: LookupError(표 키가 tables.csv에 없음)와 행 수 초과(TableTooLarge)만 거절 답으로 바꾼다.
        LLMKeyMissing·RuntimeError(키 없음·모델 호출 실패)는 장애라서 그대로 올려 SSE `error`로 끝낸다.
    """

    @tool("summarize_stat", response_format="content_and_artifact")
    def summarize_stat(question: str, table_key: str, period_start: str | None = None, period_end: str | None = None,
                       regions: list[str] | None = None, dimensions: list[str] | None = None) -> tuple[dict, StatOutput]:
        """search_tables가 찾은 표 하나(table_key = picked의 key, 예: 101_DT_1B8000H)로 질문에 답한다.
        숫자는 코드가 원자료에서 계산한다. 답(answered), 거절(refused, 이유 포함), 되묻기(clarify) 중 하나를 돌려준다.
        period_start·period_end는 질문의 기간("YYYY" 또는 "YYYYMM"), regions는 시도 공식 이름(예: "서울특별시"),
        dimensions는 분류(예: "성", "연령")다. 질문에 없으면 비워 둔다."""
        # 선택 인자 4개는 기록·채점용이다. 실전 1은 question에서 조건을 직접 읽는다(모듈 docstring 참고).
        del period_start, period_end, regions, dimensions
        started = time.perf_counter()
        rows = count_rows(table_key) if max_table_rows else None
        try:
            if rows is not None and rows > max_table_rows:
                stat = too_large(table_key, rows, max_table_rows)
            else:
                with without_checkpointer():  # 실전 1 StatState를 대화 체크포인트에 쌓지 않는다 (runtime.py 참고)
                    stat = summarize(question, table_key, llm=stat_llm, retry_interval=retry_interval)
        except LookupError as error:
            stat = not_in_catalog(error)
        writer = stream_writer()
        if writer:
            writer(("stat", {"seconds": time.perf_counter() - started, "key": table_key, "spans": stat.spans}))
        return stat.answer.model_dump(mode="json"), stat

    return summarize_stat
