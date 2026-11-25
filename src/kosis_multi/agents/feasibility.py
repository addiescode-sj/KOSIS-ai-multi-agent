"""③ 답변 가능성 에이전트: ②의 후보에 2차 리랭커(Qwen) → gate를 돌려 ④에 넘길 표를 고르거나 거절한다(S3, 결정 2).

- search_tables 툴을 여기서 닫는다: 리랭커 거절까지 끝난 최종 SearchOutput이 툴 결과로 남아야 거절로 채점된다.
- 권한은 후보 안에서 고르기·거절만. 후보 밖 표 키는 만들지 않는다(표 ID 보존). 코드 연도·지역 확인은 넣지 않는다.
- 한 질문: 시도하지 않은 후보 중 첫 표(picked 다음 candidates). 지시어·이월 턴은 직전 표 하나(검색 없음).
- 멀티쿼리: 하위 질문마다 첫 후보. 모두 같은 표면 나누지 않은 것으로 보고 원문 한 번으로 되돌린다.
- 대체 표 재시도는 계속 끔(결정 3, alt_table_budget 기본 0).
- 계산용 다듬기(S4b, MULTI_CALC_REWRITE): rerank2와 동시에 Qwen이 질문을 다듬는다. 고른 표가 있을 때만 ④가 쓴다.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from kosis_multi.agents.rewrite import CalcRewriter
from kosis_multi.parallel import run_beside
from kosis_multi.tools import Toolbox, call_tool
from kosis_rag.schemas.io import SearchOutput


def candidate_keys(search: SearchOutput | None) -> list[str]:
    """시도할 순서대로 표 키: picked 다음 나머지 candidates, 중복 없이."""
    if search is None:
        return []
    return list(dict.fromkeys(c.key for c in [*search.picked, *search.candidates]))


def next_key(state: dict) -> str | None:
    """다음에 ④에 넘길 표 키. 지시어 턴은 직전 표 하나만 쓴다(사용자가 가리킨 표를 몰래 바꾸지 않음)."""
    tried = {a["key"] for a in state["attempts"]}
    if state["reused_table"]:
        return None if tried else state["active_table"]
    search = state.get("search")
    if search is None or search.route != "found":
        return None
    return next((k for k in candidate_keys(search) if k not in tried), None)


def degraded_of(outs: list[SearchOutput]) -> bool:
    """2차 리랭커가 실패해 실전 2가 1차 순서로 대신 답했는가."""
    return any(s.get("node") == "rerank2" and s.get("stage2_error") for o in outs for s in o.spans)


def make_feasibility(tools: Toolbox, rewriter: CalcRewriter | None = None):
    def close_search(state: dict, config: RunnableConfig) -> dict:
        """②가 찾은 질문마다 search_tables(rerank2 → gate)를 한 번씩. ②를 거치지 않은 턴은 빈 dict.
        rewriter가 있으면 같은 질문들을 옆 스레드에서 동시에 다듬는다(질문마다 Qwen 1회, 순서대로)."""
        questions = state.get("search_questions")
        if not questions:
            return {}
        search_all = lambda: [call_tool(tools.search_tables, {"question": q}, config).artifact  # noqa: E731
                              for q in questions]
        rewrite_all = (lambda: [rewriter(q) for q in questions]) if rewriter is not None else None  # noqa: E731
        outs, rewrites = run_beside(search_all, rewrite_all)
        rewrites = rewrites or [None] * len(questions)
        update = {"search": outs[-1], "degraded": degraded_of(outs), "search_questions": None}  # 한 번만 닫는다
        if state.get("sub_questions"):
            update["sub_searches"] = outs
            update["sub_calc_questions"] = rewrites
        else:  # ③이 거절해도 남겨 두지만 ④에 가지 않으므로 쓰이지 않는다
            update["calc_question"] = rewrites[-1]
        return update

    def check(state: dict, config: RunnableConfig) -> dict:
        update = close_search(state, config)
        state = {**state, **update}
        if (outs := state.get("sub_searches")) is not None and state.get("sub_questions"):
            keys = [(candidate_keys(o) or [None])[0] if o.route == "found" else None for o in outs]
            if keys[0] is not None and len(set(keys)) == 1:  # 같은 표: 나누지 않은 질문으로 되돌림
                return {**update, "sub_questions": [], "sub_keys": None, "plan_key": keys[0]}
            return {**update, "sub_keys": keys}
        return {**update, "plan_key": next_key(state)}

    return check
