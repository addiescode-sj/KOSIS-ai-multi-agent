"""2차 리랭커의 멀티표 판단 (T3-51 옵션 B): 프롬프트 v4m의 `together` 필드를 읽는 selector 래퍼.

실전 2의 2차 v4는 picked(순위 목록)만 낸다. 그 안에는 같은 지표의 대안 표와 "함께 읽어야 할 표"가 섞여 있어
구분할 수 없다. v4m(`prompts/stage2-hcx-v4m.md`)은 v4 본문 1~4절을 바이트 그대로 두고, 5절과 출력에
`"together": [후보 번호 2~3개] | null`만 더한다. 판단은 2차 LLM이 하고, 검사는 이 모듈의 코드가 한다.

실전 2 코드는 고치지 않는다(vendor 직접 수정 금지). 대신:
- `apply("v4m")`이 실전 2 selector 등록부(`selectors.LLM_RERANKERS`)의 hcx·gpt를 TogetherSelector로 바꾼다.
  `apply("v4")`(기본)는 원래대로 되돌린다. backend/app/config.py stage2_llm()이 STAGE2_PROMPT로 부른다
  (서비스·e2e·L1 평가가 모두 이 함수를 거친다).
- 실전 2 rerank_stage2는 select 결과의 keys·calls·usage·reason만 메타로 옮긴다. 그래서 together는 selector가
  마지막 판단으로 들고 있고, search_tables 툴이 검색 직후 꺼내(take_together) rerank2 span에 `stage2_together`로
  붙인다. 그래프는 together_keys()로 그 값을 읽는다.

채택하면 같은 프롬프트·파서를 실전 2 PR로 옮긴다(plans/T3-51.md).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kosis_rag.llm.base import STATUS_OK
from kosis_rag.llm.openai import OpenAIClient
from kosis_rag.rerank import selectors
from kosis_rag.rerank.constants import MODEL
from kosis_rag.rerank.hcx import HcxResult, HcxSelector, parse, user_message
from kosis_rag.schemas.io import SearchOutput

PROMPT_DIR = Path(__file__).parent / "prompts"
PROMPT_FILES = {"v4m": "stage2-hcx-v4m.md"}  # v4는 실전 2 패키지 프롬프트 그대로(이 표에 없음)
DEFAULT_PROMPT = "v4"
MAX_TOGETHER = 3  # 실전 1 StatInput.tables 최대 개수 (T1-30)
TOGETHER_FIELD = "stage2_together"  # rerank2 span에 붙는 키 (표 키 목록, 없으면 [])
SWAPPED = ("hcx", "gpt")  # 번호로 표를 고르는 selector (clova reranker는 점수만 내서 together가 없음)
_ORIGINAL = {k: selectors.LLM_RERANKERS[k] for k in SWAPPED}  # apply("v4")가 되돌릴 실전 2 원래 등록


def parse_together(text: str, picked: list[int]) -> list[int] | None:
    """답에서 together 번호를 읽는다. 검사를 하나라도 통과하지 못하면 None(= 한 표 경로).

    검사: 목록이고, 정수 2~3개이고, 서로 다르고, 모두 picked 안에 있다(picked는 이미 1..n 범위로 걸러진 번호).
    번호 하나라도 틀리면 통째로 버린다: 일부만 쓰면 LLM이 뜻한 묶음과 다른 묶음이 된다.
    """
    start = text.find("{")
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(text, start)
    except ValueError:
        return None
    raw = data.get("together") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not 2 <= len(raw) <= MAX_TOGETHER:
        return None
    if not all((isinstance(x, int) and not isinstance(x, bool)) or (isinstance(x, str) and x.isdigit()) for x in raw):
        return None
    numbers = [int(x) for x in raw]
    if len(set(numbers)) != len(numbers) or not set(numbers) <= set(picked):
        return None
    return numbers


@dataclass
class TogetherSelector(HcxSelector):
    """HcxSelector와 같은 호출·파서에 v4m 프롬프트와 together 판단을 더한다. 반환값(HcxResult)은 그대로다.

    마지막 판단(표 키 목록)은 take_together()로 한 번 꺼낸다. ponytail: 판단을 selector 하나에 들고 있으므로
    동시에 두 검색이 돌면 섞일 수 있다(평가는 순차 실행, 서비스 동시 요청은 실전 2 PR로 옮길 때 메타로 해결).
    """

    prompt: str = ""
    _last: list[str] = field(default_factory=list, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __call__(self, question: str, keys: list[str]) -> HcxResult:
        if not keys:
            self._remember([])
            return HcxResult(keys=[], calls=0)
        self.client = self.client or self._client()
        result = self.client.generate(self.prompt, user_message(question, [self.text_of[k] for k in keys]))
        if result.status != STATUS_OK:
            self._remember([])
            model = getattr(self.client, "model", self.config.model if self.config is not None else MODEL)
            raise RuntimeError(f"{model} call failed ({result.status}): {result.error}")
        picked, reason = parse(result.text, len(keys))
        together = parse_together(result.text, picked) or []
        self._remember([keys[i - 1] for i in together])
        return HcxResult(keys=[keys[i - 1] for i in picked], calls=result.attempts, usage=dict(result.usage or {}),
                         reason=reason)

    def _remember(self, keys: list[str]) -> None:
        with self._lock:
            self._last = keys

    def take_together(self) -> list[str]:
        """마지막 together 판단을 꺼내고 비운다(다음 검색에 남지 않게)."""
        with self._lock:
            keys, self._last = self._last, []
        return keys


def prompt_text(version: str) -> str:
    return (PROMPT_DIR / PROMPT_FILES[version]).read_text(encoding="utf-8")


def _factories(prompt: str) -> dict[str, Callable[..., Any]]:
    def hcx(text_of: dict[str, str], config) -> TogetherSelector:
        return TogetherSelector(text_of=text_of, config=config, prompt=prompt)

    def gpt(text_of: dict[str, str], config) -> TogetherSelector:  # 실전 2 selectors.gpt와 같은 OpenAI 호환 경로
        return TogetherSelector(text_of=text_of, config=config, client=OpenAIClient(config), prompt=prompt)

    return {"hcx": hcx, "gpt": gpt}


def apply(version: str | None) -> str:
    """2차 프롬프트를 고른다. v4(기본, None·빈 값 포함) = 실전 2 원래 selector, v4m = TogetherSelector.

    실전 2가 SearchModule을 만들 때 등록부에서 selector를 고르므로, SearchModule을 만들기 전에 불러야 한다.
    Out: 적용한 버전 이름. Errors: 모르는 버전이면 ValueError.
    """
    name = (version or DEFAULT_PROMPT).strip().lower()
    selectors.LLM_RERANKERS.update({k: _ORIGINAL[k] for k in SWAPPED})  # 바꾼 두 이름만 되돌린다
    if name == DEFAULT_PROMPT:
        return name
    if name not in PROMPT_FILES:
        raise ValueError(f"unknown STAGE2_PROMPT {version!r}; known: {DEFAULT_PROMPT}, {', '.join(PROMPT_FILES)}")
    selectors.LLM_RERANKERS.update(_factories(prompt_text(name)))
    return name


def take_together(search: Any) -> list[str]:
    """검색 모듈의 2차 selector가 TogetherSelector면 마지막 판단을 꺼낸다. 아니면 [] (v4·가짜 검색)."""
    select = getattr(getattr(search, "resources", None), "select", None)
    take = getattr(select, "take_together", None)
    return take() if callable(take) else []


def with_together(out: SearchOutput, keys: list[str]) -> SearchOutput:
    """rerank2 span에 together 판단을 붙인 SearchOutput (실전 2 스키마는 그대로, span dict에 키 하나 추가)."""
    spans = [{**s, TOGETHER_FIELD: list(keys)} if s.get("node") == "rerank2" else s for s in out.spans]
    return out.model_copy(update={"spans": spans})


def together_keys(out: SearchOutput | None) -> list[str]:
    """실전 1에 함께 넘길 표 키 (2~3개), 아니면 [].

    다시 검사한다: 검색이 found이고, 키가 모두 picked 안에 있고, 2~3개이고 서로 다르다. 표 키는 실전 2 결과
    안에서만 나온다(표 ID 보존 원칙).
    """
    if out is None or out.route != "found":
        return []
    span = next((s for s in out.spans if s.get("node") == "rerank2"), None)
    keys = list((span or {}).get(TOGETHER_FIELD) or [])
    picked = {c.key for c in out.picked}
    if not 2 <= len(keys) <= MAX_TOGETHER or len(set(keys)) != len(keys) or not set(keys) <= picked:
        return []
    return keys
