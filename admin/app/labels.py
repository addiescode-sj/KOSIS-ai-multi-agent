"""Korean names of the stored codes (kosis-infra/12 8). An unknown code is shown as is and logged once."""

from __future__ import annotations

import logging

log = logging.getLogger("admin.labels")

LABELS: dict[str, dict[str, str]] = {
    "outcome": {"answered": "답함", "refused": "거절", "clarify": "되묻기", "failed": "실패", "error": "오류",
                "aborted": "답 전 이탈"},
    "reason": {"out_of_scope": "자료 없음", "no_period": "기간 없음", "no_item": "항목 없음",
               "class_mismatch": "분류 불일치", "unit_mismatch": "단위 불일치", "not_aggregatable": "합산 불가",
               "missing_value": "값 없음", "causal": "인과·예측", "unknown": "사유 없음"},
    "down": {"wrong_number": "수치가 틀림", "wrong_table": "표가 다름", "hard": "이해하기 어려움", "other": "기타"},
    "up": {"accurate_number": "수치가 정확함", "right_table": "원하는 표를 찾음", "easy": "이해하기 쉬움",
           "chart_helpful": "차트가 도움", "other": "기타"},
    "stage": {"search": "검색", "stat": "계산", "chart": "차트", "other": "기타", "agent": "에이전트"},
    "rewrite": {"new": "새 질문", "carry_over": "이어 묻기", "reference": "앞 표 다시 쓰기", "chart_only": "차트만",
                "multi": "나눠 묻기", "unknown": "결과 없음"},
    "status": {"new": "새 글", "checking": "확인 중", "fix_requested": "수정 요청", "no_issue": "문제 없음"},
    "user": {"all": "전체", "member": "회원", "guest": "비회원"},
    "tab": {"summary": "요약", "quality": "답 품질", "usage": "사용 패턴", "cost": "성능·비용"},
}
_seen: set[tuple[str, str]] = set()


def label(kind: str, code: str | None, empty: str = "–") -> str:
    """The Korean name of `code`; `empty` for None, the code itself when unknown (logged once per code)."""
    if code is None or code == "":
        return empty
    name = LABELS[kind].get(code)
    if name is None:
        if (kind, code) not in _seen:
            _seen.add((kind, code))
            log.warning("admin: no label for %s code %r", kind, code)
        return code
    return name
