"""멀티에이전트 그래프 상태.

기존 AgentState를 그대로 상속한다. 평가 하네스(evals/runners/agent.py)와 chat_result()가 읽는 필드
(stat, search, table_key, outcome, attempts, active_*, sub_results, combined)를 같은 뜻으로 채워야 같은 채점을 받는다.
여기에는 에이전트 사이를 오가는 값만 더한다. 모두 이번 턴 상태라서 ① 판단이 턴마다 비운다.
"""

from __future__ import annotations

from typing import Literal

from kosis_agent.graph.state import AgentState

# ① 판단이 정하는 경로. S1은 rag만 쓴다(direct·clarify는 S2에서 Qwen과 함께 들어옴).
Route = Literal["rag", "direct", "clarify"]


class MultiState(AgentState):
    route: Route  # ① 판단: 이번 턴을 어떻게 처리할지
    plan_key: str | None  # ③ 답변 가능성: ④에 넘길 다음 표 키 (None = 넘길 표 없음)
    sub_searches: list | None  # ② 검색: 하위 질문마다의 SearchOutput (멀티쿼리 턴만)
    sub_keys: list | None  # ③ 답변 가능성: 하위 질문마다 넘길 표 키 (없으면 None)
    timings: dict  # 에이전트별 걸린 시간 ms {"judge": .., "retriever": .., ...} (이번 턴, 여러 번 돌면 합)
