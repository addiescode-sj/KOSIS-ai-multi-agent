"""③과 동시에 도는 계산용 질문 다듬기 (S4b, 05-calc-rewrite-review.md 3-2절).

③ 2차 리랭커(Qwen)가 표를 고르는 동안, 같은 서버의 Qwen 한 번이 "다시 써야 하는지"를 판단하고 필요하면 다시 쓴다.
- 고치는 것: 기준 없는 상대 시점(작년 → 오늘 기준 연도), 지역 이름 오타(시울 → 서울), 구어 지표(아기 수 → 출생아수).
- 출력: {"reason": 한 문장(CoT), "rewrite": 문장 | null}. null이면 원 질문을 쓴다.
- 입력은 질문과 오늘 날짜뿐이다. 후보 표 이름은 주지 않는다(KOS-192: 표 이름을 베껴 틀린 형제 표로 끌림).
- 코드는 검사만 한다(safe_rewrite): 숫자·명시 연도·인식된 지역이 바뀌면 버린다.
- 쓰는 곳은 ④뿐이다. ③이 거절하면 버린다. 실패·시간 초과도 원 질문으로 간다(재시도 없음).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from kosis_rag.parse.schemas import ParsedQuery

log = logging.getLogger("kosis.multi.rewrite")

PROMPT_VERSION = "calc_rewrite_v1"
PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / f"{PROMPT_VERSION}.md"

# 기준 없는 상대 시점으로 쓰일 수 있는 말. 이 말이 원문에 있을 때만 새 연도를 허용한다.
RELATIVE = re.compile(r"재작년|작년|지난\s*해|올해|금년|전년|(?<!\d)\d{1,2}\s*년\s*전")
YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


class RewriteReply(BaseModel):
    reason: str = ""
    rewrite: str | None = None


RewriteLLM = Callable[[str], RewriteReply]  # 질문 -> RewriteReply
CalcRewriter = Callable[[str], "str | None"]  # 질문 -> 검사를 통과한 다시 쓴 질문 (없으면 None)


def today_kst() -> date:
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def system_prompt(today: date) -> str:
    y = today.year
    values = {"today": today.isoformat(), "this_year": str(y), "last_year": str(y - 1),
              "two_years_ago": str(y - 2), "ten_years_ago": str(y - 10)}
    text = PROMPT_FILE.read_text(encoding="utf-8")
    for name, value in values.items():  # JSON 예시의 중괄호가 있어 str.format 대신 자리만 바꾼다
        text = text.replace("{" + name + "}", value)
    return text


def parse_rewrite_reply(text: str) -> RewriteReply:
    """응답의 첫 JSON 객체를 RewriteReply로 검증한다(<think>…</think>·코드 블록·앞뒤 말 무시)."""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    start = text.find("{")
    if start < 0:
        raise ValueError(f"no JSON in reply: {text[:80]!r}")
    data, _ = json.JSONDecoder().raw_decode(text, start)
    return RewriteReply.model_validate(data)


def make_rewrite_llm(chat_model, *, today: Callable[[], date] = today_kst) -> RewriteLLM:
    """LangChain chat model(Qwen, 2차 리랭커 서버) → RewriteLLM. 호출 1회, JSON 한 줄."""

    def ask(question: str) -> RewriteReply:
        reply = chat_model.invoke([("system", system_prompt(today())), ("human", f"질문: {question}")])
        return parse_rewrite_reply(str(reply.content))

    return ask


def _numbers(text: str) -> list[str]:
    """연도와 상대 시점("10년 전")의 숫자를 뺀 숫자들."""
    text = RELATIVE.sub(" ", YEAR.sub(" ", text))
    return sorted(NUMBER.findall(text))


def safe_rewrite(original: str, rewrite: str | None, parse: Callable[[str], ParsedQuery]) -> str | None:
    """다시 쓴 질문이 지켜야 할 것을 지켰으면 그 문장, 아니면 None(원 질문을 씀).

    - 비었거나 원문과 같거나 원문보다 지나치게 길면 버린다.
    - 원문의 명시 연도는 모두 남아야 한다. 새 연도는 원문에 상대 시점 말이 있을 때만 허용한다.
    - 연도·상대 시점 밖의 숫자("최근 5년", "상위 3개")는 그대로 남아야 한다.
    - 원문에서 인식된 시도는 그대로여야 한다. 새 시도는 원문에서 시도를 못 읽었을 때만(오타) 허용한다.
    """
    if not rewrite or not rewrite.strip():
        return None
    new = rewrite.strip()
    if new == original.strip() or len(new) > 2 * len(original) + 20:
        return None
    old_years, new_years = set(YEAR.findall(original)), set(YEAR.findall(new))
    if not old_years <= new_years:
        return None
    if new_years - old_years and not RELATIVE.search(original):
        return None
    if _numbers(original) != _numbers(new):
        return None
    old_regions, new_regions = set(parse(original).regions), set(parse(new).regions)
    if old_regions and old_regions != new_regions:
        return None
    return new


def make_calc_rewriter(rewrite_llm: RewriteLLM, parse: Callable[[str], ParsedQuery]) -> CalcRewriter:
    """질문 하나를 다듬는다. Qwen 실패·형식 오류·검사 실패는 모두 None(원 질문)이고 예외를 올리지 않는다."""

    def rewrite(question: str) -> str | None:
        try:
            got = rewrite_llm(question)
        except Exception as error:  # noqa: BLE001 - 다듬기는 선택 단계: 실패하면 원 질문
            log.warning("calc rewrite failed, using the original question: %s: %s", type(error).__name__, error)
            return None
        return safe_rewrite(question, got.rewrite, parse)

    return rewrite
