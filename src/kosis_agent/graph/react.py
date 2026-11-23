"""ReAct 단계 판단 (T3-03 옵션 B): 단계 경계마다 Thought → Action 한 번, Observation은 실제 툴 결과.

교재 7.1.2의 다섯 요소를 이 그래프에 맞춘 것:
- Thought      : 판단 근거 한 줄. 상태 `trace`에 단계별로 남는다(SSE·UI·평가에서 보임).
- Action       : 단계마다 코드가 정한 집합 중 하나. LLM이 집합 밖 값을 내면 그 판단을 버린다.
                   understand  template | clarify | rewrite | search
                   sufficiency sufficient | retry
                   split       keep | merge
- Action Input : 재작성 질의. 원문·이전 질문에 있는 숫자·시도·낱말만 쓸 수 있다(grounded, LLM이 조건을 지어내지 않음).
- Observation  : search_tables·summarize_stat 툴의 실제 결과. LLM이 만들지 않는다.
- Final Answer : 실전 1 답 문장(StatAnswer.text) 또는 코드 고정 템플릿. LLM이 답 문장을 쓰지 않는다.

LLM은 규칙이 판단하지 못한 경우에만 부른다(규칙 우선, T3-27과 같은 원칙). LLM이 없거나(키·서버 없음) 형식이 틀리면
규칙 결과로 돌아간다. 모델은 2차 리랭커와 같은 OpenAI 호환 서버의 Qwen이 기본이다(backend/app/config.py agent_llm).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from kosis_agent.graph.context import CHART_WORDS, REGION_ALIASES, _new_words, _numbers, leftover
from kosis_rag.parse.schemas import ParsedQuery

Steps = Literal["off", "a", "b"]  # off: LLM 판단 없음 · a: 이해 + 검색 거절 후 재작성 · b: a + 충분성 판단 + 분해 검토
Slot = Literal["지표", "지역", "기간"]
SLOTS = ("지표", "지역", "기간")


def _slots(value) -> list[str]:
    """missing 정규화: Qwen이 null이나 목록 밖 이름("연도" 등)을 내도 판단 전체를 버리지 않고 아는 슬롯만 남긴다(2026-11-23 실측)."""
    return [v for v in (value or []) if v in SLOTS]

# ---------------------------------------------------------------- 코드 고정 문장 (LLM 아님)
TEMPLATES = {
    "greeting": ("안녕하세요. 저는 국가데이터처(KOSIS) 통계표를 찾아 숫자로 답하고 차트로 보여 드리는 챗봇이에요. "
                 "\"2024년 서울 출생아 수 알려줘\"처럼 지표·지역·기간을 넣어 물어봐 주세요."),
    "not_statistics": ("저는 KOSIS 통계표로 답하는 챗봇이라 통계와 관련 없는 질문에는 답하지 못해요. "
                       "찾고 싶은 통계의 지표·지역·기간을 알려 주세요."),
}


def clarify_text(missing: list[str]) -> str:
    """되묻기 문장. 빠진 슬롯 이름만 코드가 끼운다."""
    slots = "·".join(dict.fromkeys(missing)) or "지표·지역·기간"
    return f"어떤 통계를 찾으시나요? {slots}을(를) 조금 더 구체적으로 알려 주시면 찾아볼게요."


# 인사·기능 문의 (내용 낱말이 없을 때만 템플릿. "도움"이 든 통계 질문은 지표 낱말이 있어 여기에 걸리지 않는다)
GREETING = re.compile(r"^\s*(안녕|하이|헬로|hello|hi\b|고마워|고맙|감사|반가|누구(야|세요|니)|넌\s*뭐|너는\s*뭐|"
                      r"뭘\s*할\s*수|무엇을\s*할\s*수|뭐\s*할\s*수|사용법|도움말)", re.IGNORECASE)


# ---------------------------------------------------------------- LLM 출력 계약
class UnderstandStep(BaseModel):
    thought: str = Field(description="판단 근거 한 줄")
    action: Literal["template", "clarify", "rewrite", "search"] = Field(
        description="template: 통계와 무관한 질문(인사·잡담 등) · clarify: 통계 질문이지만 무엇을 찾는지 알 수 없음 · "
                    "rewrite: 통계 질문인데 문장을 다듬어야 검색할 수 있음 · search: 그대로 검색해도 됨")
    kind: Literal["greeting", "not_statistics"] | None = Field(default=None, description="template일 때만: greeting 또는 not_statistics")
    missing: list[Slot] = Field(default_factory=list, description="clarify일 때 빠진 것: 지표, 지역, 기간 중")

    _missing = field_validator("missing", mode="before")(lambda cls, v: _slots(v))


class RewriteStep(BaseModel):
    thought: str = Field(description="무엇을 어떻게 고쳤는지 한 줄")
    question: str = Field(description="검색에 쓸 한 문장 질문. 주어진 문장에 있는 낱말·숫자·지역만 쓴다")


class SufficiencyStep(BaseModel):
    thought: str = Field(description="판단 근거 한 줄")
    action: Literal["sufficient", "retry"] = Field(
        description="sufficient: 지표가 분명해서 통계표 색인에서 표를 찾아 답할 수 있음 · retry: 지표가 불분명해 다시 써야 함")
    missing: list[Slot] = Field(default_factory=list, description="retry일 때 빠진 것: 지표, 지역, 기간 중")

    _missing = field_validator("missing", mode="before")(lambda cls, v: _slots(v))


class SplitStep(BaseModel):
    thought: str = Field(description="판단 근거 한 줄")
    action: Literal["keep", "merge"] = Field(
        description="keep: 서로 다른 지표라 표가 따로 필요함 · merge: 같은 지표를 항목·지역끼리 비교하는 한 질문")


def parse_step(text: str, model: type[BaseModel]) -> BaseModel:
    """응답의 첫 JSON 객체를 model로 검증한다(context.parse_llm_reading과 같은 방식). 실패하면 ValueError."""
    start = text.find("{")
    if start < 0:
        raise ValueError(f"no JSON in reply: {text[:80]!r}")
    data, _ = json.JSONDecoder().raw_decode(text, start)
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise ValueError(str(error)) from error


def _schema(model: type[BaseModel]) -> str:
    return "\n".join(f"- {name}: {f.description}" for name, f in model.model_fields.items())


ROLE = ("너는 국가데이터처(KOSIS) 통계표 검색 챗봇의 판단 단계다. 색인에는 인구·출생·사망·혼인·가구·고용·물가·소득 등 "
        "국가 통계표가 있다. 숫자를 만들거나 질문에 없는 지역·연도·지표를 덧붙이지 마라. "
        "다른 말 없이 JSON 객체 한 줄로만 답하라. 키:\n")


class Judge:
    """Qwen(OpenAI 호환 chat model) 위의 단계 판단. 메서드마다 호출 1회, JSON 한 줄로 받아 코드가 검증한다.

    with_structured_output·tool calling을 쓰지 않는 이유: 경량화 Qwen 서버에서 tool calling이 켜져 있는지 확인되지
    않았고, T3-27에서 같은 이유로 JSON 한 줄 방식을 택했다(context.make_llm_reader).
    """

    def __init__(self, chat_model):
        self.chat_model = chat_model

    def _ask(self, model: type[BaseModel], task: str, human: str) -> BaseModel:
        reply = self.chat_model.invoke([("system", ROLE + _schema(model) + "\n\n" + task), ("human", human)])
        return parse_step(str(reply.content), model)

    def understand(self, question: str, previous: str | None) -> UnderstandStep:
        task = "사용자 질문을 보고 다음 행동을 하나 고른다. 애매하면 search를 고른다(검색 단계가 범위 밖 질문을 거절한다)."
        human = (f"직전 질문: {previous}\n" if previous else "") + f"이번 질문: {question}"
        return self._ask(UnderstandStep, task, human)

    def rewrite(self, source: str, feedback: str | None) -> RewriteStep:
        task = ("주어진 문장들을 통계표 검색에 쓸 한 문장 질문으로 다시 쓴다. 되묻기 답이 있으면 원래 질문에 그 답을 채워 넣는다. "
                "주어진 문장에 없는 낱말·숫자·지역은 쓰지 마라.")
        human = source + (f"\n이전 시도의 문제: {feedback}" if feedback else "")
        return self._ask(RewriteStep, task, human)

    def sufficiency(self, question: str) -> SufficiencyStep:
        task = "이 질문 하나로 통계표를 찾아 답할 수 있는지 판단한다. 지역·기간이 없어도 지표가 분명하면 sufficient다(최신값으로 답함)."
        return self._ask(SufficiencyStep, task, f"질문: {question}")

    def split(self, question: str, subs: list[str]) -> SplitStep:
        task = "규칙이 이 질문을 여러 하위 질문으로 나눴다. 서로 다른 지표면 keep, 한 지표의 항목·지역 비교면 merge를 고른다."
        return self._ask(SplitStep, task, f"질문: {question}\n하위 질문: {json.dumps(subs, ensure_ascii=False)}")


def safe(call: Callable[[], BaseModel]) -> BaseModel | None:
    """LLM 판단 한 번. 장애·형식 오류면 None(호출한 쪽이 규칙 결과로 돌아간다)."""
    try:
        return call()
    except Exception:  # noqa: BLE001 - 판단 단계 장애는 규칙 경로로 대신한다(턴을 끊지 않음)
        return None


# ---------------------------------------------------------------- 규칙 판단
def rule_understand(question: str, parsed: ParsedQuery) -> UnderstandStep | None:
    """질의 이해 규칙. 판단하지 못하면 None(→ LLM).

    - 인사·기능 문의이고 지표·조건이 없으면 template(greeting).
    - 지표 낱말·조건(시도·기간·분류)·차트 말이 있거나, 조건 표현을 지우고도 내용이 6자 이상 남으면 search
      ("태어난 아이 수"처럼 지표 낱말이 없는 통계 질문이 있어서 남는 내용도 본다. 범위 밖이면 실전 2 gate가 거절한다).
    - 그 밖(짧고 내용 없는 말: "요즘 어때?")은 판단 보류.
    """
    slots = bool(parsed.measures or parsed.regions or parsed.period_start or parsed.period_end or parsed.dimensions)
    if GREETING.search(question) and not slots:
        return UnderstandStep(thought="인사·기능 문의", action="template", kind="greeting")
    if slots or CHART_WORDS.search(question) or len(leftover(question)) >= 6:
        return UnderstandStep(thought="통계 질문(지표·조건 있음)", action="search")
    return None


def grounded(text: str, sources: str, parse: Callable[[str], ParsedQuery]) -> bool:
    """재작성 문장이 sources에 있는 숫자·시도·낱말만으로 만들어졌는가 (context.verify_reading과 같은 검사)."""
    if not text.strip() or not _numbers(text) <= _numbers(sources):
        return False
    if not set(parse(text).regions) <= set(parse(sources).regions):
        return False
    for alias in REGION_ALIASES:
        text = text.replace(alias, " ")
    return not _new_words(text, sources)
