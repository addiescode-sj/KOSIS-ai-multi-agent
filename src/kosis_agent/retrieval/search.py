"""search_tables 툴: 실전 2 검색 모듈(kosis_rag.api.SearchModule)을 LangChain 툴로 감싼다 (T3-23).

실전 2 검색 그래프(parse → condition → [embed] → retrieve_* → fuse → [rerank1] → [rerank2] → gate)는 고치지 않는다.
SearchSteps를 넘기면(T3-60) 메인 그래프 candidates 노드가 rerank1까지 먼저 돌려 둔 후보에 rerank2 → gate만 돈다
(search_steps.py). 넘기지 않으면 한 번에 부른다. 검색 알고리즘이 실전 2 어디에 있는지는 같은 폴더 README.md에 정리했다.

툴 계약 (evals/evaluators/agent.py가 읽는 형태):
- 인자: question
- content: SearchOutput JSON. 하네스는 이 안의 route == "refuse"로 거절을, picked[0].key로 상태(표)를 읽는다.
- artifact: SearchOutput 객체. 노드가 꺼내 AgentState["search"]에 둔다.
response_format="content_and_artifact"라서 ToolCall 형태로 부르면 ToolMessage(content, artifact)가 돌아온다.
ReAct의 ToolNode도 같은 방식으로 부르므로, T3-07에서 이 툴을 그대로 쓴다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from kosis_agent.observability.tracing import STAGE2_MODEL
from kosis_agent.runtime import stream_writer, without_checkpointer
from kosis_rag.schemas.io import SearchInput, SearchOutput

if TYPE_CHECKING:
    from kosis_agent.retrieval.search_steps import SearchSteps


def stream_search(search: Any, question: str) -> SearchOutput:
    """실전 2 검색 그래프 전체를 한 번 돈다. 노드가 끝날 때마다 (노드 이름, trace 항목)을 stream writer로 내보낸다."""
    writer = stream_writer()
    out: SearchOutput | None = None
    with without_checkpointer():  # 실전 2 SearchState를 대화 체크포인트에 쌓지 않는다 (runtime.py 참고)
        for name, value in search.stream(SearchInput(question=question)):
            if name == "output":  # 실전 2 SearchModule.stream의 마지막 항목 = 최종 SearchOutput
                out = value
            elif writer:
                writer((name, value))
    if out is None:  # 실전 2 계약상 일어나지 않지만, None을 그대로 넘기면 노드에서 알아보기 어려운 오류가 난다
        raise RuntimeError("search module stream ended without an output")
    return out


def make_search_tables_tool(search: Any, steps: SearchSteps | None = None) -> BaseTool:
    """실전 2 검색 모듈을 감싼 search_tables 툴을 만든다.

    In: search = kosis_rag.api.SearchModule (테스트에서는 .stream(SearchInput)만 있는 가짜).
        steps = 후보·고르기를 나눠 도는 어댑터(T3-60). 있으면 보관한 후보에 rerank2 → gate만 돈다(없으면 전체).
    Out: search_tables 툴. 실행하면 실전 2 노드가 끝날 때마다 (노드 이름, trace 항목)을 stream writer로 내보낸다.
        이 이벤트는 /chat/stream의 SSE 노드 이벤트가 된다(이벤트 이름·내용은 리팩토링 전과 같음).
    Errors: 실전 2의 예외(벡터 DB 연결 실패, LLMKeyMissing 등)는 그대로 올라간다.
    """

    @tool("search_tables", response_format="content_and_artifact")
    def search_tables(question: str, config: RunnableConfig) -> tuple[dict, SearchOutput]:
        """KOSIS 통계표를 찾는다. route(found | refuse), 고른 표(picked: key·표 이름·기간·출처), 후보(candidates)를 돌려준다."""
        # config는 LangChain이 넣는다(툴 인자 스키마에 없음). 보관한 후보를 대화(thread_id)별로 찾는 데 쓴다
        out = steps.rerank_and_gate(question, config) if steps is not None else stream_search(search, question)
        return out.model_dump(mode="json"), out

    # T3-31: rerank2 trace 항목에는 stage 2 이름(clova 등)만 있어서, chat span의 모델 이름을 툴 metadata로 넘긴다
    stage2 = getattr(getattr(search, "resources", None), "stage2_llm", None)
    if getattr(stage2, "model", None):
        search_tables.metadata = {STAGE2_MODEL: stage2.model}
    return search_tables
