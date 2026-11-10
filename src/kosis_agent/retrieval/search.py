"""search_tables 툴: 실전 2 검색 모듈(kosis_rag.api.SearchModule)을 LangChain 툴로 감싼다 (T3-23).

실전 2 검색 그래프(parse → condition → [embed] → retrieve_* → fuse → [rerank1] → [rerank2] → gate)는 고치지 않고
한 번에 부른다. 검색 알고리즘이 실전 2 어디에 있는지는 같은 폴더 README.md에 정리했다.

툴 계약 (evals/evaluators/agent.py가 읽는 형태):
- 인자: question
- content: SearchOutput JSON. 하네스는 이 안의 route == "refuse"로 거절을, picked[0].key로 상태(표)를 읽는다.
- artifact: SearchOutput 객체. 노드가 꺼내 AgentState["search"]에 둔다.
response_format="content_and_artifact"라서 ToolCall 형태로 부르면 ToolMessage(content, artifact)가 돌아온다.
ReAct의 ToolNode도 같은 방식으로 부르므로, T3-07에서 이 툴을 그대로 쓴다.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from kosis_agent.runtime import stream_writer, without_checkpointer
from kosis_rag.schemas.io import SearchInput, SearchOutput


def make_search_tables_tool(search: Any) -> BaseTool:
    """실전 2 검색 모듈을 감싼 search_tables 툴을 만든다.

    In: search = kosis_rag.api.SearchModule (테스트에서는 .stream(SearchInput)만 있는 가짜).
    Out: search_tables 툴. 실행하면 실전 2 노드가 끝날 때마다 (노드 이름, trace 항목)을 stream writer로 내보낸다.
        이 이벤트는 /chat/stream의 SSE 노드 이벤트가 된다(이벤트 이름·내용은 리팩토링 전과 같음).
    Errors: 실전 2의 예외(Chroma 연결 실패, LLMKeyMissing 등)는 그대로 올라간다.
    """

    @tool("search_tables", response_format="content_and_artifact")
    def search_tables(question: str) -> tuple[dict, SearchOutput]:
        """KOSIS 통계표를 찾는다. route(found | refuse), 고른 표(picked: key·표 이름·기간·출처), 후보(candidates)를 돌려준다."""
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
        return out.model_dump(mode="json"), out

    return search_tables
