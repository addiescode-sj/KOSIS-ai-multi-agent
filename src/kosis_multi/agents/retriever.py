"""② 검색 에이전트: 실전 2 parse → retrieve(조건 필터·목록들·fuse) → rerank1로 후보 표만 찾는다. LLM 없음(S3).

표는 고르지 않는다. 후보는 search_steps 보관함에 두고, ③이 search_tables 툴 안에서 rerank2 → gate로 마저 고른다.
그래서 ②는 툴 span을 남기지 않는다(채점 대상은 ③의 search_tables 하나, 기존과 같은 횟수·인자).

- 새 질문: 사용자 원문으로 찾는다.
- 이월 턴인데 직전 표가 표 불일치로 거절함: 다시 쓴 독립 질문으로 다시 찾는다(한 번만).
- 이월 턴인데 ①이 직전 표를 쓰지 않기로 함(지표만 바꾼 이월, 직전 표에 없는 시도): 처음부터 다시 쓴 질문으로 찾는다.
- 멀티쿼리 턴: 하위 질문마다 순서대로 찾는다(결정 5: 하위 질문 병렬은 뺌).
S4 비동기 A: ①이 원문으로 미리 찾아 둔 결과가 있으면 retrieve_candidates가 그것을 기다려 쓴다(같은 질문일 때만).
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from kosis_multi.search_steps import SearchSteps


def make_retriever(steps: SearchSteps):
    def retrieve(state: dict, config: RunnableConfig) -> dict:
        if state.get("sub_questions"):
            questions = list(state["sub_questions"])
            update: dict = {}
        else:
            fallback = bool(state.get("carried_over") and (state["attempts"] or not state.get("reused_table")))
            questions = [state["resolved_question"] if fallback else state["question"]]
            # 다시 찾는 턴: 이제부터 직전 표가 아니라 새 검색 후보에서 고른다
            update = {"reused_table": False, "fallback_searched": True} if fallback else {}
        for question in questions:
            steps.retrieve_candidates(question, config)
        return {**update, "search_questions": questions}

    return retrieve
