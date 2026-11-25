"""멀티에이전트 그래프 상태.

기존 AgentState를 그대로 상속한다. 평가 하네스(evals/runners/agent.py)와 chat_result()가 읽는 필드
(stat, search, table_key, outcome, attempts, active_*, sub_results, combined)를 같은 뜻으로 채워야 같은 채점을 받는다.
여기에는 에이전트 사이를 오가는 값만 더한다. 모두 이번 턴 상태라서 ① 판단이 턴마다 비운다.
"""

from __future__ import annotations

from typing import Literal

from kosis_agent.graph.state import AgentState

# ① 판단이 정하는 경로 (결정 4: 되묻기는 실전 1 몫이라 없음)
Route = Literal["rag", "direct"]


class MultiState(AgentState):
    route: Route  # ① 판단: 이번 턴을 어떻게 처리할지
    route_by: str  # 경로를 누가 정했나 rule | llm | none (none = 판단 보류인데 Qwen 없음·실패 → rag)
    direct_text: str | None  # route가 direct일 때의 답 (Qwen 문장, 숫자 검사를 통과한 것 또는 코드 고정 문장)
    plan_key: str | None  # ③ 답변 가능성: ④에 넘길 다음 표 키 (None = 넘길 표 없음)
    search_questions: list | None  # ② 검색: 후보를 찾아 둔 질문들 (③이 search_tables로 닫으면 None)
    sub_searches: list | None  # ③ 답변 가능성: 하위 질문마다의 SearchOutput (멀티쿼리 턴만)
    calc_question: str | None  # S4b: ④가 쓸 다듬은 질문 (None = 원 질문). ③이 rerank2와 동시에 만든다
    sub_calc_questions: list | None  # S4b: 멀티쿼리 하위 질문마다의 다듬은 질문 (없으면 None)
    sub_keys: list | None  # ③ 답변 가능성: 하위 질문마다 넘길 표 키 (없으면 None)
    timings: dict  # 에이전트별 걸린 시간 ms {"judge": .., "retriever": .., ...} (이번 턴, 여러 번 돌면 합)
