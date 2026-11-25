"""① 판단 앞의 후속 질문 규칙 (11/25 서버 점검): 기존 해석 규칙(read_turn)이 놓치는 세 경우를 코드로 정한다.

1. 지표만 바꾼 짧은 질문("이혼은?"): 기존 규칙 4는 지표 낱말이 있으면 새 질문으로 보고 원문으로 검색해서, 직전 질문의
   연도·시도를 잃었다. 직전 질문이 연도·시도를 말했으면 그 조건을 앞에 붙인 독립 질문("2024년 이혼은?")으로 새로 찾는다.
   표는 바뀌므로 직전 표를 쓰지 않는다.
2. 시도를 더해 비교("부산이랑 비교해줘"): 기존 규칙 5는 시도를 바꿔 끼워서 부산 값만 답했다. 직전 시도 옆에 새 시도를
   더한 문장("2024년 서울과 부산 출생아 수 비교해줘")으로 직전 표에 묻는다.
3. 직전 표에 없는 시도(region_gap): 이월로 다시 쓴 질문이 직전 표에 없는 시도를 물으면("2024년 출생아 수"의 전국 표 +
   "서울은?") 직전 표를 쓰지 않고 다시 쓴 질문으로 새로 찾는다. 실전 1은 시도 칸이 없는 표에서 전국 값을 그대로 읽어
   거절하지 않기 때문이다. 찾은 표에도 없으면 검색·실전 1이 거절한다.

1·2는 규칙만이고 LLM을 부르지 않는다. 판단하지 못하면 None을 돌려 기존 read_turn이 정한다.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from kosis_agent.graph.context import NEW_QUESTION_CHARS, REGION_ALIASES, Reading, leftover, single_year
from kosis_rag.parse.schemas import ParsedQuery
from kosis_rag.shared.vocab import SIDO

RegionsOf = Callable[[str], "set[str] | None"]  # 표 키 -> 표가 가진 시도 이름 (시도 칸이 없으면 None)

# 기간을 따로 말하는 질문은 직전 연도를 붙이지 않는다("이혼 추이는?")
OWN_PERIOD = re.compile(r"추이|변화|동안|부터|까지|최근|역대|매년|해마다|연도별|년별")
COMPARE = re.compile(r"비교|대비|견주")
# "부산이랑", "부산하고", "부산과": 시도 바로 뒤의 함께 묶는 말
WITH = r"\s*(?:이랑|랑|하고|과|와|도\s*같이|도\s*함께)(?![가-힣])"
REQUEST_END = re.compile(r"\s*(?:(?:을|를)\s*)?(?:알려\s*(?:줘|주세요|줄래)|보여\s*(?:줘|주세요)|(?:은|는|이|가)(?:\s*(?:얼마|몇)\s*[가-힣]*|(?=\s*[?.!])))?\s*[?.!]*\s*$")


def _with_particle(word: str) -> str:
    """받침이 있으면 "과", 없으면 "와"."""
    last = word[-1]
    if "가" <= last <= "힣":
        return word + ("과" if (ord(last) - 0xAC00) % 28 else "와")
    return word + "과"


def _surface(text: str, region: str) -> str | None:
    """문장 안에서 그 시도를 쓴 표현(공식 이름 또는 줄임말), 없으면 None."""
    for alias in REGION_ALIASES:
        if (alias == region or alias in SIDO.get(region, [])) and alias in text:
            return alias
    return None


def carry_conditions(question: str, previous: str, parse: Callable[[str], ParsedQuery]) -> str | None:
    """지표만 바꾼 짧은 질문에 직전 질문의 연도·시도를 붙인 독립 질문. 붙일 것이 없거나 해당하지 않으면 None.

    예: "2024년 출생아 수 알려줘" + "이혼은?" → "2024년 이혼은?"
        "2023년 부산 혼인 건수는?" + "사망자 수는?" → "2023년 부산광역시 사망자 수는?"
    """
    cur = parse(question)
    if not cur.measures or cur.regions or cur.period_start or cur.period_end or cur.recent or OWN_PERIOD.search(question):
        return None
    if len(leftover(question)) >= 2 * NEW_QUESTION_CHARS:  # 긴 문장은 스스로 선 새 질문
        return None
    prev = parse(previous)
    if set(cur.measures) <= set(prev.measures):  # 같은 지표면 기존 규칙에 맡긴다
        return None
    parts = []
    if year := single_year(prev):
        parts.append(f"{year}년")
    if len(prev.regions) == 1:
        parts.append(prev.regions[0])
    if not parts:
        return None
    return " ".join([*parts, question.strip()])


def compare_regions(question: str, previous: str, parse: Callable[[str], ParsedQuery]) -> str | None:
    """직전 시도에 새 시도를 더해 비교하는 독립 질문. 해당하지 않으면 None.

    예: "2024년 서울 출생아 수 알려줘" + "부산이랑 비교해줘" → "2024년 서울과 부산광역시 출생아 수 비교해줘"
    """
    cur = parse(question)
    if len(cur.regions) != 1 or cur.measures:
        return None
    new = cur.regions[0]
    said = _surface(question, new) or new
    joined = re.search(re.escape(said) + WITH, question)
    if not COMPARE.search(question) and not joined:
        return None
    if len(leftover(COMPARE.sub(" ", question))) >= NEW_QUESTION_CHARS:
        return None
    prev = parse(previous)
    if len(prev.regions) != 1 or prev.regions[0] == new:
        return None
    old = _surface(previous, prev.regions[0])
    if old is None:
        return None
    text = previous.replace(old, f"{_with_particle(old)} {new}", 1)
    return REQUEST_END.sub("", text).rstrip() + " 비교해줘"


def follow_up(question: str, previous: str | None, parse: Callable[[str], ParsedQuery]) -> Reading | None:
    """규칙 1·2. 직전 질문이 없거나 해당하지 않으면 None(기존 read_turn이 정함)."""
    if not previous:
        return None
    if text := compare_regions(question, previous, parse):
        return Reading(use_previous_table=True, resolved_question=text, by="rule")
    if text := carry_conditions(question, previous, parse):
        return Reading(use_previous_table=False, resolved_question=text, by="rule")
    return None


def region_gap(resolved: str, table_key: str | None, parse: Callable[[str], ParsedQuery],
               regions_of: RegionsOf | None) -> bool:
    """규칙 3: 다시 쓴 질문이 묻는 시도 중 직전 표에 없는 것이 있는가. 표 정보를 모르면 False(기존 동작)."""
    if regions_of is None or not table_key:
        return False
    asked = set(parse(resolved).regions) - {"전국"}
    if not asked:
        return False
    try:
        held = regions_of(table_key)
    except KeyError:
        return False
    return held is None or not asked <= held
