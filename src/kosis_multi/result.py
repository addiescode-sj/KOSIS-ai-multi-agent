"""멀티에이전트 그래프의 마지막 상태 → 서비스 응답(ChatResult). 기존 kosis_agent.graph.state.chat_result를 감싼다.

기존 chat_result와 다른 점 (kosis_agent는 고치지 않는다)
- direct 턴(인사·사용법, S2): 이번 턴에 검색이 없어서 기존 함수는 state["search"]에서 KeyError가 난다(첫 턴)거나
  직전 턴의 검색 결과를 이번 답처럼 보여 준다(다음 턴). 여기서는 검색 없이 답 문장만 돌려준다.
  route = "direct", answer_origin = "direct", 후보·표·차트 없음.
- 계산용 다듬기(S4b)로 답한 턴: answer는 안내 줄이 붙은 마지막 메시지, resolved_question은 실전 1에 실제로 넘긴
  다듬은 질문이다(ChatResult.resolved_question의 뜻 = "실전 1에 넘긴 문장").
그 밖의 턴은 기존 chat_result와 같다.

서비스 연결(backend/app/main.py가 이 그래프와 이 함수를 쓰게 하는 스위치)은 아직 하지 않았다.
"""

from __future__ import annotations

from kosis_agent.graph.state import ChatResult, chat_result


def multi_chat_result(state: dict) -> ChatResult:
    answer = state["messages"][-1].content
    if state.get("route") == "direct":
        return ChatResult(question=state.get("question") or answer, route="direct", no_answer=False, answer=answer,
                          answer_origin="direct", outcome=state.get("outcome"),
                          resolved_question=state.get("resolved_question"), interpreted_by=state.get("interpreted_by"))
    result = chat_result(state)
    rewritten = state.get("calc_question")
    if rewritten and result.answer_origin == "stat":
        result = result.model_copy(update={"answer": answer, "resolved_question": rewritten})
    return result
