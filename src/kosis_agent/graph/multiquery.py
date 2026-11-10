"""멀티쿼리 분해: 한 턴 안에 서로 다른 표가 필요한 질문 여러 개를 하위 질문으로 나눈다 (T3-28, plans/T3-28.md 옵션 C).

예: "2024년 경기도 출생아 수와 외국인주민 수 알려줘" → ["2024년 경기도 출생아 수", "2024년 경기도 외국인주민 수 알려줘"]

나누는 방식 (split_question):
1. 강한 구분자("?" 문장 끝, ",", "그리고", "또")로 자른다. 자른 조각이 질문이 아니면(지표 낱말도 없고 남는 내용도
   짧음: "서울이랑 경기도 비교해줘") 앞 조각에 다시 붙인다. 그래서 "2025년 2인 가구 수, 서울이랑 경기도 비교해줘"는
   나누지 않는다(같은 표 비교는 실전 1 compare가 답함).
2. 각 조각을 약한 구분자("와·과·랑·이랑·하고·및")로 한 번 더 자르되, **잘린 조각마다 실전 2가 지표 낱말(measures)을 읽고
   그 지표가 서로 다를 때만** 나눈다. "서울과 부산 조사망률"은 지표가 하나라 나누지 않는다.
3. 짧은 조각("사망자 수는?")에 연도·지역이 없으면 첫 조각의 연도·지역 표현을 코드가 앞에 붙인다(조건 공유).
4. 계산 요청: 끝에 붙은 "둘의 차이는?"·"둘을 더하면?" 같은 말(지표 낱말 없음, 두 값을 가리키는 말 있음)이나 약한 구분자로 자른 마지막 조각의 계산어는
   op(diff | sum | ratio)가 된다. 완전한 하위 질문 안의 "차이"는 그 질문의 것이라 op로 보지 않는다.
5. 판단 보류(deferred, _unsure): 규칙으로는 하나인데 여러 지표일 수 있음("외국인주민은 몇 명이고 1인 가구는 몇 가구야?",
   "주민등록세대수와 인구밀도"처럼 실전 2가 한쪽 지표를 못 읽는 "X와 Y"). splitter(LLM)가 있으면 한 번 묻고, 결과는 코드가 검사한다(verify_split). 통과하지 못하면 나누지 않는다.

나눠도 같은 표를 찾으면 그래프가 원문 한 번으로 되돌린다(main_graph.py multi 노드). 표 키·숫자는 LLM이 정하지 않는다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from pydantic import BaseModel, Field

from kosis_agent.graph.context import NEW_QUESTION_CHARS, REGION_ALIASES, _new_words, _numbers, leftover
from kosis_agent.services.combine import Op
from kosis_rag.parse.schemas import ParsedQuery

STRONG = re.compile(r"(?<=[?？])\s+(?:(?:그리고|또)\s+)?|\s*(?<!\d)[,，](?!\d)\s*(?:(?:그리고|또)\s+)?|\s+(?:그리고|또)\s+")
WEAK = re.compile(r"(?<=[가-힣])(?:이랑|랑|하고|와|과)\s+|\s+및\s+")
OP_WORDS: list[tuple[re.Pattern, Op]] = [  # 먼저 맞는 것이 이긴다
    (re.compile(r"차이|빼면|뺀\s*값"), "diff"),
    (re.compile(r"더하면|더한\s*값|합치면|합하면|합은"), "sum"),
    (re.compile(r"나누면|몇\s*배"), "ratio"),
]  # "합계출산율"·"비율"·"비중"은 지표 이름이라 계산어로 보지 않는다(retrieval_golden_v2에서 확인)
# 앞 질문들의 값 둘을 가리키는 말. 이 말 없이 계산어만 있는 조각("…어디가 높고, 차이는 얼마야?")은 앞 질문의 일부다
BOTH = re.compile(r"둘|두\s*(?:값|개|수|수치|숫자|지표|통계)|합쳐|합치면|모두|전부")
# 판단 보류의 조건: 연결 어미("몇 명이고 …", "…이며 …")로 두 질문을 이은 것. 지표 낱말이 둘 이상이라도 이 연결이
# 없으면("일반가구 중 1인 가구 비중") 한 질문이다(골든 90문항에서 확인, tests/test_multiquery.py)
CONNECTIVE = re.compile(r"[가-힣](?:이고|고|이며|며)\s")
# 한 표 안의 비교를 묻는 말. "X와 Y" 판단 보류에서 뺀다(골든 90문항 중 비교 문항이 LLM에 가지 않게)
COMPARISON = re.compile(r"차이|비교|어디|어느|중\s|대비|증감|증가|감소|늘었|줄었|높|낮|많|적|각각|비율|비중|추이|변화|변했")
# 기간 표현: "2024년", "2025년 3월", "2015~2020년", "2015년부터 2020년까지"
PERIOD = re.compile(r"\d{4}\s*년(?:\s*\d{1,2}\s*월)?(?:\s*(?:부터|~|-)\s*\d{4}\s*년(?:\s*까지)?)?|\d{4}\s*~\s*\d{4}\s*년")


class Split(BaseModel):
    """분해 결과. sub_questions가 하나면 나누지 않은 것이다. by = rule | llm | none."""

    sub_questions: list[str]
    op: Op | None = None
    deferred: bool = False
    by: str = "none"


def _op_of(text: str) -> Op | None:
    return next((op for pattern, op in OP_WORDS if pattern.search(text)), None)


def _strip_op(text: str) -> str:
    """조각 끝의 계산 요청("…를 더하면 몇 명이야?")을 지운다. 실전 1에는 값 질문만 넘긴다."""
    for pattern, _ in OP_WORDS:
        if m := pattern.search(text):
            text = text[:m.start()]
    return re.sub(r"\s*(?:을|를|의|에서|에)?\s*$", "", text).strip()


def _is_question(piece: str, parsed: ParsedQuery) -> bool:
    """조각이 따로 물을 수 있는 질문인가: 지표 낱말이 있거나, 자기 연도가 있고 조건 말을 지우고도 내용이 남는다
    ("2025년 경기도에서 태어난 아이는 몇 명이에요?"). 지표도 연도도 없는 조각("시도별 동태 통계로.",
    "부산 영도구와 부산진구 차이는?")은 앞 질문을 꾸미는 말이다."""
    has_period = bool(parsed.period_start or parsed.period_end)
    return bool(parsed.measures) or (has_period and len(leftover(piece)) >= NEW_QUESTION_CHARS)


def _bare(parsed: ParsedQuery) -> bool:
    return not (parsed.measures or parsed.regions or parsed.period_start or parsed.period_end or parsed.dimensions)


def _weak_split(piece: str, parse: Callable[[str], ParsedQuery]) -> list[str]:
    parts = [p.strip() for p in WEAK.split(piece) if p.strip()]
    if len(parts) < 2:
        return [piece]
    measures = [frozenset(parse(p).measures) for p in parts]
    if any(not m for m in measures) or len(set(measures)) != len(measures):
        return [piece]  # 지표가 비었거나 같은 지표 = 같은 표 비교일 가능성이 크다
    return parts


def _share(piece: str, first: str, parse: Callable[[str], ParsedQuery], fragment: bool) -> str:
    """조각에 첫 조각의 기간·지역 표현을 붙인다("사망자 수는?" → "2024년 서울 사망자 수는?").

    약한 구분자로 잘린 조각("…출생아 수와 외국인주민 수")은 늘 조각이라 붙인다. 강한 구분자로 잘린 조각은 짧을 때만
    붙인다. 완전한 질문에 빠진 조건은 그 질문의 뜻이다(기본값은 실전 1이 정함)."""
    if not fragment and len(leftover(piece)) >= NEW_QUESTION_CHARS:
        return piece
    mine, head = parse(piece), parse(first)
    prefix = []
    if not (mine.period_start or mine.period_end) and (m := PERIOD.search(first)):
        prefix.append(m.group(0))
    if not mine.regions and head.regions:
        surface = next((a for a in REGION_ALIASES if a in first and a != "전국"), None)
        if surface:
            prefix.append(surface)
    return " ".join([*prefix, piece]) if prefix else piece


def _unsure(question: str, parse: Callable[[str], ParsedQuery]) -> bool:
    """규칙이 나누지 않았지만 여러 지표일 수 있는 턴 (규칙 5, LLM에 넘김).

    - 연결 어미로 이은 두 지표: "외국인주민은 몇 명이고 1인 가구는 몇 가구야?"
    - "X와 Y"인데 실전 2가 한쪽 지표를 못 읽음: "주민등록세대수와 인구밀도", "매출액과 수출액". 양쪽 모두 조건 말을 지우고
      내용이 남아야 한다. "서울과 부산 조사망률"처럼 지역·연도만 이은 것은 비교라서 넘기지 않는다. 양쪽 지표가 같으면
      ("현재 1인 가구 수와 2050년 1인 가구 전망") 같은 지표의 비교라서 넘기지 않는다.
    """
    if len(set(parse(question).measures)) >= 2 and CONNECTIVE.search(question):
        return True
    if COMPARISON.search(question) or _op_of(question):
        # "남자와 여자 차이", "서울과 경기도 중 어디" = 한 표 안의 비교(실전 1 compare). 계산어가 있는 "X와 Y를 더하면"은
        # LLM이 나눠도 계산(op)을 정하지 못하므로 넘기지 않는다(규칙이 지표를 읽으면 규칙 2·4로 나뉨)
        return False
    parts = [p.strip() for p in WEAK.split(question) if p.strip()]
    if len(parts) < 2 or any(len(leftover(p)) < 2 for p in parts):
        return False
    measures = [frozenset(parse(p).measures) for p in parts]
    return any(not m for m in measures) or len(set(measures)) == len(measures)


def split_by_rules(question: str, parse: Callable[[str], ParsedQuery]) -> Split:
    """규칙 1~5 (모듈 docstring)."""
    pieces: list[str] = []
    op: Op | None = None
    for raw in (p.strip() for p in STRONG.split(question)):
        if not raw:
            continue
        parsed = parse(raw)
        if _bare(parsed) and BOTH.search(raw) and (found := _op_of(raw)):
            op = found  # "둘의 차이는?" 같은 계산 요청 조각 (두 값을 가리키는 말이 있어야 함)
            continue
        own_conditions = parsed.period_start or parsed.period_end or parsed.regions
        if pieces and (not _is_question(raw, parsed) or (_op_of(raw) and not own_conditions)):
            # 질문이 아닌 조각("…, 서울이랑 경기도 비교해줘")과 자기 조건 없이 계산만 덧붙인 조각("…, 지역별로 다
            # 더하면 몇 가구야?")은 앞 조각의 일부다
            pieces[-1] = f"{pieces[-1]}, {raw}"
            continue
        pieces.append(raw)
    if pieces and not _is_question(pieces[0], parse(pieces[0])) and len(pieces) > 1:
        pieces[:2] = [f"{pieces[0]}, {pieces[1]}"]
    subs: list[tuple[str, bool]] = []  # (하위 질문, 약한 구분자로 잘린 조각인가)
    for piece in pieces:
        parts = _weak_split(piece, parse)
        if len(parts) > 1 and op is None and (found := _op_of(parts[-1])):
            op, parts[-1] = found, _strip_op(parts[-1])  # "…와 출생아 수를 더하면 몇 명이야?"
        subs.extend((p, len(parts) > 1) for p in parts)
    if len(subs) < 2:
        return Split(sub_questions=[question], deferred=_unsure(question, parse))
    first = subs[0][0]
    out = [first, *(_share(s, first, parse, fragment) for s, fragment in subs[1:])]
    return Split(sub_questions=out, op=op if len(out) == 2 else None, by="rule")


# ---------------------------------------------------------------- LLM 분해 (판단 보류 턴만)
class LLMSplit(BaseModel):
    sub_questions: list[str] = Field(description="원문을 표 하나로 답할 수 있는 질문들로 나눈 것. 나눌 필요가 없으면 원문 하나.")


SplitReader = Callable[[str], LLMSplit]  # 질문 -> LLMSplit
# LLM이 하위 질문 끝에 붙여도 되는 묻는 말("…는 얼마인가요?", "몇 개야?"). 지표·조건 낱말이 아니라서 허용한다
# (2026-11-10 gpt-4o-mini 실측: "주민등록세대수와 인구밀도를 같이 알려줘" → "…세대수는 몇 개인가요?")
QUESTION_ENDING = re.compile(r"(?:몇|얼마|개|명|건|가구)?(?:인가요|이에요|예요|이야|야|입니까|나요|돼|요)?")

SPLIT_EXAMPLES = [  # 빌더·검증 세트와 다른 표현으로 쓴다(외우지 않게)
    ("2022년 대전 사망자는 몇 명이고 혼인은 몇 건이야?",
     {"sub_questions": ["2022년 대전 사망자는 몇 명이야?", "2022년 대전 혼인은 몇 건이야?"]}),
    ("2021년 광주와 울산의 출생아 수 비교해줘", {"sub_questions": ["2021년 광주와 울산의 출생아 수 비교해줘"]}),
]


def verify_split(split: LLMSplit, question: str, parse: Callable[[str], ParsedQuery]) -> bool:
    """LLM 하위 질문이 원문만으로 만들어졌는가: 둘 이상, 서로 다름, 숫자·시도·한글 낱말이 모두 원문에 있음."""
    subs = [s.strip() for s in split.sub_questions if s.strip()]
    if len(subs) < 2 or len(set(subs)) != len(subs):
        return False
    regions, numbers = set(parse(question).regions), _numbers(question)
    for sub in subs:
        if not set(parse(sub).regions) <= regions or not _numbers(sub) <= numbers:
            return False
        text = sub
        for alias in REGION_ALIASES:
            text = text.replace(alias, " ")
        if any(not QUESTION_ENDING.fullmatch(w) for w in _new_words(text, question)):
            return False
    return True


def make_llm_splitter(chat_model) -> SplitReader:
    """LangChain chat model → SplitReader. 호출 1회, JSON 한 줄(T3-27 make_llm_reader와 같은 이유로 구조화 출력 안 씀)."""
    examples = "\n".join(f"질문: {q}\n답: {json.dumps(a, ensure_ascii=False)}" for q, a in SPLIT_EXAMPLES)
    system = ("너는 국가통계 챗봇의 질문 분해기다. 한 질문에 서로 다른 통계 지표를 묻는 질문이 여러 개 섞여 있으면, 지표마다 "
              "따로 물을 수 있는 질문으로 나눈다. 같은 지표를 여러 지역·기간으로 비교하는 질문은 나누지 않는다. "
              "원문에 있는 낱말과 숫자만 쓰고, 공유하는 연도·지역은 각 질문에 그대로 옮겨 적는다. "
              f"다른 말 없이 JSON 객체 한 줄로만 답하라. 키: sub_questions (문자열 목록)\n\n예시:\n{examples}")

    def read(question: str) -> LLMSplit:
        reply = str(chat_model.invoke([("system", system), ("human", f"질문: {question}")]).content)
        start = reply.find("{")
        if start < 0:
            raise ValueError(f"no JSON in reply: {reply[:80]!r}")
        data, _ = json.JSONDecoder().raw_decode(reply, start)
        return LLMSplit.model_validate(data)

    return read


def split_question(question: str, parse: Callable[[str], ParsedQuery], splitter: SplitReader | None = None) -> Split:
    """규칙 → (판단 보류이고 splitter가 있으면) LLM 한 번 → 검증. 실패하면 나누지 않는다."""
    split = split_by_rules(question, parse)
    if not split.deferred or splitter is None:
        return split
    try:
        got = splitter(question)
    except Exception:  # noqa: BLE001 - LLM 장애면 나누지 않는다(지금 동작)
        return split
    if not verify_split(got, question, parse):
        return split
    return Split(sub_questions=[s.strip() for s in got.sub_questions if s.strip()], by="llm")
