"""Context Resolver: 이번 질문을 직전 대화에 비춰 어떻게 실행할지 정한다 (T3-23 지시어 규칙 + T3-27 이월·차트).

정하는 것은 네 가지다(Reading):
- use_previous_table : 검색 없이 직전 턴의 표(active_table)를 다시 쓸지
- resolved_question  : 실전 1에 넘길 문장 (이월이면 직전 질문에 이번 조건을 채운 독립 질문, 아니면 원문)
- chart_type         : 요청한 차트 종류 (bar | line | pie, 종류 말이 없으면 None)
- chart_only         : 숫자는 그대로 두고 차트만 다시 그려 달라는 요청인지

해석 방식 (plans/T3-27.md 옵션 B, `mode`로 바꿈):
- rules  : 아래 규칙만. 판단하지 못하면 원문으로 새 검색(PR #9 동작)
- hybrid : 규칙 먼저, 규칙이 판단하지 못한 턴만 LLM 한 번 (기본)
- llm    : 직전 표가 있으면 매 턴 LLM 한 번 (규칙은 지시어·차트 판단에도 쓰지 않음)

규칙 (LLM 0):
1. 직전 표가 없으면 새 질문이다.
2. 차트 말("막대그래프", "꺾은선", "차트로 그려")만 있고 다른 내용이 없으면 차트만 다시 그린다.
3. "그 표", "이 통계" 같은 지시어가 있으면 직전 표에 이번 질문을 그대로 묻는다(PR #9 규칙).
4. 조건 표현(연도·시도·정정어·조사·지시어·차트어)을 지우고도 내용 낱말이 5자 이상 남거나, 실전 2가 지표 낱말을
   읽으면 새 질문이다. 골든 90문항 중 13개는 지표 낱말이 없어서("태어난 아이" 등), 지표 낱말만으로는 새 질문을
   가를 수 없다. 그래서 "남는 내용"을 같이 본다.
5. 남는 내용이 없고 시도·연도·정정어가 있으면 이월이다("부산은?", "아니 2023년으로"). 직전 질문 문장에서 바뀐
   표현(시도 이름, "YYYY년")을 새 값으로 바꿔 끼운 문장을 만든다. 바꿀 표현을 직전 문장에서 못 찾거나 기간이
   범위면 판단하지 않는다(틀린 문장을 만들지 않음).
6. 그 밖("남자는?", "작년은?")은 판단하지 않는다 → hybrid면 LLM, rules면 원문으로 새 검색.

LLM 결과는 코드가 검사한다(verify_reading): 독립 질문의 시도·분류·지표 낱말과 숫자가 직전 질문 또는 이번 질문에
있는 것이어야 한다. 하나라도 벗어나면 그 해석을 버린다(LLM이 지어낸 조건이 실전 1에 들어가지 않게).
표 키는 어떤 경우에도 LLM이 고르지 않는다(active_table 또는 새 검색 결과).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from kosis_rag.parse.schemas import ParsedQuery
from kosis_rag.shared.vocab import SIDO

ChartType = Literal["bar", "line", "pie"]
Mode = Literal["rules", "hybrid", "llm"]

# ---------------------------------------------------------------- 지시어 (T3-23)
# 지시어(그·이·저·같은·위·방금·아까·앞) + 표를 뜻하는 말. 뒤에는 조사·공백·문장 끝·문장부호만 온다.
# - (?<![가-힣]) : 지시어 앞이 한글이면 다른 낱말의 일부다 (예: "높이 표로" 의 "이").
# - (?=...)     : "표" 뒤가 "준·본·시" 같은 글자면 "표준·표본·표시" 같은 다른 낱말이다 (예: "이 표준인구").
REFERENCE = re.compile(
    r"(?<![가-힣])(그|이|저|같은|위|방금|아까|앞)\s*(?:의\s*)?(통계표|표|통계|자료)"
    r"(?=$|\s|[.,?!]|에서|에|으로|로|를|을|은|는|이|가|의|도|만|와|과|랑|하고)"
    r"|(?<![가-힣])(거기서|여기서|거기|여기)(?=$|\s|[.,?!]|에서|는|도|만)"
)


def refers_to_previous_table(question: str) -> bool:
    """질문이 직전 턴의 표를 가리키면 True."""
    return REFERENCE.search(question) is not None


# ---------------------------------------------------------------- 차트 요청 (T3-27)
CHART_WORDS = re.compile(r"그래프|차트|시각화|그려|막대|꺾은선|선\s*그래프|원형|파이|도표")
CHART_KINDS: list[tuple[re.Pattern, ChartType]] = [  # 먼저 맞는 것이 이긴다
    (re.compile(r"막대|바\s*차트|바\s*그래프"), "bar"),
    (re.compile(r"꺾은선|선\s*그래프|라인"), "line"),
    (re.compile(r"원형|파이"), "pie"),
]


def chart_request(question: str) -> tuple[bool, ChartType | None]:
    """(차트를 요청했는가, 요청한 종류). 종류 말 없이 "그래프로 보여줘"면 (True, None) = 실전 1이 고른 종류."""
    if not CHART_WORDS.search(question):
        return False, None
    return True, next((kind for pattern, kind in CHART_KINDS if pattern.search(question)), None)


# ---------------------------------------------------------------- 이월 판단 (T3-27)
REGION_ALIASES = sorted({alias for name, aliases in SIDO.items() for alias in (name, *aliases)} | {"전국"},
                        key=len, reverse=True)
YEAR = re.compile(r"(\d{4})\s*년(?:도)?")
CORRECTION = re.compile(r"(?<![가-힣])(아니|아니고|말고|대신|다시|기준으로|기준)(?![가-힣])")
# 지운 뒤 "남는 내용"을 셀 때 빼는 말 (내용 낱말이 아님)
# - FILLER_WORDS: 맞장구·요청 표현. 어디에 있든 지운다.
# - PARTICLES  : 한 글자 조사·어미. 낱말 끝에서만 지운다("자연증가건수"의 "가"처럼 낱말 안의 글자는 남긴다).
FILLER_WORDS = re.compile(
    r"어때요|어때|어떤가요|어떤가|어떻게\s*돼|그럼|그러면|그리고|이걸|이거|그걸|그거|저거|"
    r"해줘|해주세요|알려줘|알려주세요|보여줘|보여주세요|줘|주세요|좀|\d{1,2}\s*월|[?!.,~]"
)
PARTICLES = re.compile(r"(은요|는요|이요|도요|으로|쪽은|쪽|로|은|는|요|이|가|의|에|도|만|값)(?=\s|$)")
NEW_QUESTION_CHARS = 5  # 조건 표현을 지우고도 한글 내용이 이만큼 남으면 새 질문


def leftover(question: str) -> str:
    """조건 표현(시도·연도·정정어·지시어·차트어·조사·요청 표현)을 지우고 남는 한글 내용."""
    text = REFERENCE.sub(" ", question)
    text = YEAR.sub(" ", text)
    text = re.sub(r"\d+", " ", text)
    for alias in REGION_ALIASES:
        text = text.replace(alias, " ")
    text = CHART_WORDS.sub(" ", text)
    text = CORRECTION.sub(" ", text)
    text = FILLER_WORDS.sub(" ", text)
    for _ in range(2):  # "부산 쪽은" 처럼 조사가 두 겹이면 두 번 벗긴다
        text = PARTICLES.sub(" ", text)
    return "".join(re.findall(r"[가-힣A-Za-z]+", text))


def single_year(parsed: ParsedQuery) -> str | None:
    """질문이 연도 하나를 말하면 그 연도("YYYY"), 범위·여러 해·월이면 None."""
    if parsed.period_start and parsed.period_start == parsed.period_end and len(parsed.period_start) == 4:
        return parsed.period_start
    return None


def carry_over(previous: str, prev: ParsedQuery, current: ParsedQuery) -> str | None:
    """직전 질문 문장에 이번 질문의 시도·연도를 바꿔 끼운 독립 질문. 바꿀 수 없으면 None(판단하지 않음).

    예: "2025년 경기도에서 태어난 아이는 몇 명이에요?" + "부산은?" → "2025년 부산광역시에서 태어난 아이는 몇 명이에요?"
        "2025년 경기도에서 태어난 아이는 몇 명이에요?" + "아니 2023년으로" → "2023년 경기도에서 태어난 아이는 몇 명이에요?"
    """
    text, changed = previous, False
    if current.regions:
        if len(current.regions) != 1:
            return None  # 여러 시도 비교로 바꾸는 것은 문장 치환으로 하지 않는다
        names = {*prev.regions, "전국"}
        surfaces = [a for a in REGION_ALIASES if any(a == n or a in SIDO.get(n, []) for n in names) and a in text]
        if not surfaces:
            return None
        text = text.replace(surfaces[0], current.regions[0], 1)
        changed = True
    year = single_year(current)
    if year:
        old = single_year(prev) or (prev.period_start if prev.period_start and prev.period_start == prev.period_end
                                    else None)
        if old is None or YEAR.search(text) is None:
            return None  # 직전 질문이 기간 범위이거나 연도가 없으면 바꾸지 않는다
        text = re.sub(rf"{old[:4]}(\s*년)", rf"{year}\1", text)
        changed = True
    return text if changed and text != previous else None


# ---------------------------------------------------------------- 해석 결과와 검증
class Reading(BaseModel):
    """이번 턴을 어떻게 실행할지. by = 누가 정했나 (rule | llm | none: 직전 표 없음·판단 보류 후 원문)."""

    use_previous_table: bool = False
    resolved_question: str
    chart_type: ChartType | None = None
    chart_only: bool = False
    by: Literal["rule", "llm", "none"] = "none"


class LLMReading(BaseModel):
    """LLM 해석기의 출력 (with_structured_output 스키마). 필드 설명은 LLM에게도 보인다."""

    standalone_question: str = Field(description="직전 질문의 조건을 채워 그것만 읽어도 뜻이 통하는 질문. 새 주제면 이번 질문 그대로.")
    use_previous_table: bool = Field(description="직전 질문과 같은 통계(같은 표)를 조건만 바꿔 묻는 후속 질문이면 true.")
    chart_type: ChartType | None = Field(default=None, description="차트를 요청했으면 bar, line, pie 중 하나. 종류를 말하지 않았거나 차트 요청이 없으면 null.")
    chart_only: bool = Field(default=False, description="숫자는 그대로 두고 직전 답을 차트로만 다시 그려 달라는 요청이면 true.")


LLMReader = Callable[[str, str], LLMReading]  # (직전 질문, 이번 질문) -> LLMReading


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


TOKEN_ENDINGS = re.compile(r"(에서|에게|으로|이에요|예요|이야|인가요|인가|입니까|은|는|이|가|을|를|의|에|로|도|만|요)$")


def _new_words(text: str, sources: str) -> list[str]:
    """text의 한글 낱말 중 sources(직전 질문 + 이번 질문)에 없는 것. 낱말 끝 조사·어미는 떼고 비교한다."""
    out = []
    for token in re.findall(r"[가-힣]+", text):
        stem = TOKEN_ENDINGS.sub("", token) or token
        if stem not in sources and token not in sources:
            out.append(token)
    return out


def verify_reading(reading: LLMReading, previous: str, question: str, parse: Callable[[str], ParsedQuery]) -> bool:
    """LLM 독립 질문이 직전·이번 질문만으로 만들어졌는가. 하나라도 어기면 False(그 해석은 버림).

    - 숫자: 직전·이번 질문에 있는 숫자만 (LLM이 만든 숫자 = 버그)
    - 시도: 실전 2 규칙으로 읽은 시도가 직전·이번 질문의 시도 안에 있어야 함 (지역을 지어내지 않음)
    - 낱말: 한글 낱말이 직전·이번 질문 어딘가에 있어야 함 (다른 지표·분류로 바꿔 말하지 않음).
      처음에는 실전 2가 읽은 지표·분류 낱말 집합을 비교했지만, "태어난 아이" → "출생아"처럼 같은 뜻을 바꿔 말해도
      걸리고 "남자는?"의 "남자"는 분류로 안 읽혀서, 낱말 자체를 비교하는 쪽으로 바꿨다(tests/test_workflow.py).
    """
    got, prev, cur = parse(reading.standalone_question), parse(previous), parse(question)
    if not set(got.regions) <= set(prev.regions) | set(cur.regions):
        return False
    if not _numbers(reading.standalone_question) <= _numbers(previous) | _numbers(question):
        return False
    # 시도 이름은 위에서 시도 단위로 검사했으므로 낱말 비교에서는 뺀다("부산" → "부산광역시"처럼 공식 이름으로 풀어 써도 됨)
    text = reading.standalone_question
    for alias in REGION_ALIASES:
        text = text.replace(alias, " ")
    return not _new_words(text, previous + " " + question)


def make_llm_reader(chat_model) -> LLMReader:
    """LangChain chat model(예: langchain_naver.ChatClovaX) → LLMReader. 호출 1회, 구조화 출력(LLMReading)."""
    structured = chat_model.with_structured_output(LLMReading)
    system = ("너는 국가통계 챗봇의 대화 해석기다. 직전 질문과 이번 질문을 보고, 이번 질문이 직전 질문의 조건(지표·기간·지역)을 "
              "이어받는 후속 질문인지 판단해 독립 질문으로 다시 쓴다. 독립 질문은 직전 질문과 이번 질문에 있는 낱말과 숫자만으로 "
              "만든다. 같은 뜻이라도 다른 낱말로 바꾸지 말고, 질문에 없는 지역·지표·분류·숫자를 덧붙이지 마라.")

    def read(previous: str, question: str) -> LLMReading:
        return structured.invoke([("system", system), ("human", f"직전 질문: {previous}\n이번 질문: {question}")])

    return read


# ---------------------------------------------------------------- 해석기
def read_turn(question: str, *, active_table: str | None, active_question: str | None,
              parse: Callable[[str], ParsedQuery], mode: Mode = "hybrid", llm: LLMReader | None = None) -> Reading:
    """이번 턴의 Reading. 규칙 → (판단 보류면) LLM → (그래도 없으면) 원문으로 새 검색."""
    wants_chart, kind = chart_request(question)
    fresh = Reading(resolved_question=question, chart_type=kind)
    if not active_table:  # 규칙 1
        return fresh
    previous = active_question or ""
    if mode != "llm":
        decided = _rules(question, previous, parse, wants_chart, kind)
        if decided is not None:
            return decided
    if mode == "rules" or llm is None or not previous:
        return fresh
    try:
        got = llm(previous, question)
    except Exception:  # noqa: BLE001 - LLM 장애는 PR #9 동작(원문으로 새 검색)으로 돌아간다
        return fresh
    if got.chart_only:
        return Reading(use_previous_table=True, resolved_question=previous, chart_type=got.chart_type,
                       chart_only=True, by="llm")
    if not got.use_previous_table:
        # 새 질문이면 LLM이 다시 쓴 문장이 아니라 사용자 원문으로 검색한다(LLM의 영향을 이월 턴으로 좁힘)
        return Reading(resolved_question=question, chart_type=got.chart_type or kind, by="llm")
    if not verify_reading(got, previous, question, parse):
        return fresh
    return Reading(use_previous_table=True, resolved_question=got.standalone_question,
                   chart_type=got.chart_type or kind, by="llm")


def _rules(question: str, previous: str, parse: Callable[[str], ParsedQuery], wants_chart: bool,
           kind: ChartType | None) -> Reading | None:
    """규칙 2~6. 판단하지 못하면 None."""
    rest = leftover(question)
    current = parse(question)
    slots = bool(current.regions or current.period_start or current.period_end or current.dimensions or current.recent)
    if wants_chart and not rest and not slots:  # 규칙 2: 차트만
        return Reading(use_previous_table=True, resolved_question=previous or question, chart_type=kind,
                       chart_only=True, by="rule")
    if refers_to_previous_table(question):  # 규칙 3: 지시어
        return Reading(use_previous_table=True, resolved_question=question, chart_type=kind, by="rule")
    if len(rest) >= NEW_QUESTION_CHARS or current.measures:  # 규칙 4: 새 질문
        return Reading(resolved_question=question, chart_type=kind, by="rule")
    if not rest and previous and (current.regions or current.period_start or CORRECTION.search(question)):
        rewritten = carry_over(previous, parse(previous), current)  # 규칙 5: 이월
        if rewritten is not None:
            return Reading(use_previous_table=True, resolved_question=rewritten, chart_type=kind, by="rule")
    return None  # 규칙 6: 판단 보류
