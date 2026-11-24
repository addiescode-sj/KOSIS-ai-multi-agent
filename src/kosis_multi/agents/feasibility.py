"""③ 답변 가능성 에이전트: ②의 후보 중 ④에 넘길 표를 고른다. 후보 밖 표 키는 만들지 않는다(표 ID 보존).

지금(뼈대)은 기존 그래프와 같은 규칙만 둔다:
- 한 질문: 시도하지 않은 후보 중 첫 표(picked 다음 candidates). 지시어·이월 턴은 직전 표 하나.
- 멀티쿼리: 하위 질문마다 첫 후보. 모두 같은 표면 나누지 않은 것으로 보고 원문 한 번으로 되돌린다.

TODO(S3, 결정 2·3): 후보 표마다 질문의 기간·지역·분류가 표에 있는지 코드로 확인해, 없는 표는 넘기기 전에 거른다
    (권한 = 후보 안에서 고르기·거절만 안). 기존 대체 표 재시도(alt_table_budget)를 이 확인으로 대신할지 결정 대기.
TODO(S3): 표 메타데이터(기간·시도·분류)를 어디서 읽을지 확인 (실전 2 resources / 실전 1 tables.csv).
"""

from __future__ import annotations

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


def make_feasibility():
    def check(state: dict) -> dict:
        if (outs := state.get("sub_searches")) is not None and state.get("sub_questions"):
            keys = [(candidate_keys(o) or [None])[0] if o.route == "found" else None for o in outs]
            if keys[0] is not None and len(set(keys)) == 1:  # 같은 표: 나누지 않은 질문으로 되돌림
                return {"sub_questions": [], "sub_keys": None, "plan_key": keys[0]}
            return {"sub_keys": keys}
        return {"plan_key": next_key(state)}

    return check
