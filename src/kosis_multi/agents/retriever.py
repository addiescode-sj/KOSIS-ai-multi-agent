"""② 검색 에이전트: search_tables 툴로 표 후보를 받는다. 표는 고르지 않는다(③이 고름).

- 새 질문: 사용자 원문으로 찾는다.
- 이월 턴인데 직전 표가 표 불일치로 거절함: 다시 쓴 독립 질문으로 다시 찾는다(한 번만).
- 멀티쿼리 턴: 하위 질문마다 찾는다. 지금은 순서대로. TODO(S4, 비동기 B): 하위 질문 병렬 + 동시 호출 상한.
TODO(S4, 비동기 A): ①과 동시에 원문으로 미리 검색해 두고, rag 경로면 그 결과를 쓴다.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from kosis_multi.tools import Toolbox, call_tool
from kosis_rag.schemas.io import SearchOutput


def degraded_of(outs: list[SearchOutput]) -> bool:
    """2차 리랭커가 실패해 실전 2가 1차 순서로 대신 답했는가."""
    return any(s.get("node") == "rerank2" and s.get("stage2_error") for o in outs for s in o.spans)


def make_retriever(tools: Toolbox):
    def retrieve(state: dict, config: RunnableConfig) -> dict:
        if state.get("sub_questions"):
            outs = [call_tool(tools.search_tables, {"question": q}, config).artifact for q in state["sub_questions"]]
            return {"sub_searches": outs, "search": outs[-1], "degraded": degraded_of(outs)}
        fallback = bool(state.get("carried_over") and state["attempts"])
        question = state["resolved_question"] if fallback else state["question"]
        out: SearchOutput = call_tool(tools.search_tables, {"question": question}, config).artifact
        update = {"search": out, "degraded": degraded_of([out])}
        if fallback:  # 이제부터 직전 표가 아니라 새 검색 후보에서 고른다
            update.update(reused_table=False, fallback_searched=True)
        return update

    return retrieve
