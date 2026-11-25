"""① 판단 에이전트: 이번 턴을 어떻게 처리할지 정한다.

정하는 것: 경로(route = rag | direct), 실전 1에 넘길 문장(이월이면 다시 쓴 독립 질문), 직전 표 재사용, 하위 질문, 차트 요청.

경로 판단 (S2, 결정 4):
0. 인사·감사·사용법 표현뿐인 메시지(차트 요청 없음)는 이월 해석보다 먼저 direct로 정한다. 그래야 직전 표가 있는
   대화에서 "고마워요"가 이월 해석기(LLM)를 거쳐 직전 질문으로 다시 답하는 일이 없다.
1. 기존 해석 규칙(read_turn)으로 이월·지시어·차트를 먼저 읽는다. 직전 표를 쓰는 턴과 차트 요청 턴은 모두 rag다.
2. 새 질문이면 규칙으로 경로를 정한다(route_by_rules):
   - 실전 2 규칙이 지표·시도·기간·분류 중 하나라도 읽으면 rag (통계 정보 요청은 무조건 검색).
   - 인사·감사·사용법·기능 질문 표현이고 그 밖의 내용이 없으면 direct.
   - 둘 다 아니면 판단 보류.
3. 판단 보류 턴만 Qwen에게 한 번 묻는다(JSON 한 줄: route + reply). 실패하거나 Qwen이 없으면 rag(지금 동작).
   직전 표가 있는 대화에서는 Qwen의 direct를 받지 않는다("남자는?" 같은 후속 질문이 문맥 없이 direct로 빠지지 않게).
4. direct면 답 문장을 Qwen이 쓴다(규칙이 direct로 정한 턴도 Qwen 한 번). 안전장치(safe_reply):
   - 답에 숫자(아라비아 숫자·한글 수사·"절반" 등)가 있거나 실전 2 규칙이 지표·시도를 읽으면 버리고 코드 고정 문장을
     쓴다. 숫자는 ④만 만든다.
   - 빈 답·호출 실패·Qwen 없음도 고정 문장.
   통계 사실을 말하지 말라는 것은 프롬프트로 막는다(숫자 검사가 마지막 방어선).
5. rag 새 질문만 멀티쿼리 분해(split_question)를 한다.
6. 후속 질문 규칙(agents/followup.py, 11/25 서버 점검): read_turn 전에 "이혼은?"(직전 연도·시도를 붙여 새 검색),
   "부산이랑 비교해줘"(직전 시도에 더해 비교)를 코드로 정한다. 이월로 다시 쓴 질문이 직전 표에 없는 시도를 물으면
   직전 표를 쓰지 않고 다시 쓴 질문으로 새로 찾는다(region_gap). 이렇게 새로 찾는 턴은 경로 판단·멀티쿼리 분해를 하지 않는다.
되묻기는 여기서 하지 않는다(표를 본 실전 1이 규칙으로 함, 결정 4).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Literal

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from kosis_agent.graph.context import LLMReader, Mode, Reading, chart_request, read_turn
from kosis_agent.graph.multiquery import SplitReader, split_question
from kosis_multi.agents.followup import RegionsOf, follow_up, region_gap
from kosis_rag.parse.schemas import ParsedQuery

Route = Literal["rag", "direct"]
log = logging.getLogger("kosis.multi.judge")

# 통계 정보 요청이 아닌 메시지: 인사·감사·작별·사용법·기능·정체 질문. 이 표현을 지운 뒤 남는 내용이 없어야 direct다.
SMALL_TALK = re.compile(
    r"안녕(하세요|하십니까|히\s*계세요)?|반가(워요|워|습니다)|반갑습니다|하이|헬로|hello|hi|"
    r"고마(워요|워|웠어요|습니다)|고맙습니다|감사(해요|합니다|해)|땡큐|thank(s| you)|"
    r"잘\s*(가|있어)(요)?|수고(하세요|했어요|하셨습니다)|"
    r"(넌|너는|당신은)\s*(누구|뭐)(야|예요|니|세요)?|"
    r"(뭘|무엇을|뭐|어떤\s*걸|어떤\s*것을?)\s*(물어볼|물어\s*볼|질문할|할)\s*수\s*있(어|어요|나요|니|습니까)|"
    r"(사용법|도움말|쓰는\s*법|사용\s*방법)(이|은|는|을|좀|요)?|어떻게\s*(써|쓰(는|면|나|죠|지)|사용해|사용하(는|면|나))",
    re.IGNORECASE)
# 요청 말투("알려줘", "좀")는 내용으로 보지 않는다
POLITE = re.compile(r"좀|알려\s*(줘|주세요|줄래)(요)?|가르쳐\s*(줘|주세요)|설명해\s*(줘|주세요)|해\s*(줘|주세요)")
REST = re.compile(r"[가-힣A-Za-z]+")
# 숫자 검사: 아라비아 숫자, 한글 수사(단위 있든 없든 만·억·조 단위 큰 수, "천만에요"는 제외), 고유어 수사 + 단위,
# 비율 말("절반", "프로"). "년·세"만 붙은 한글 수사는 넣지 않는다("오세요"·"일년 내내" 같은 말투가 걸림).
NUMBERS = re.compile(
    r"\d|"
    r"(?<![가-힣])(수|약|몇)?[일이삼사오육칠팔구십백천]*[만억조](?!에요|에)|"
    r"(?<![가-힣])(수|약|몇)?[일이삼사오육칠팔구십백천만억조]+\s*(명|개|가구|건|원|퍼센트|%|배)|"
    r"(?<![가-힣])(한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|수십|수백|수천|수만)\s*(명|개|배|가구|건|곳)|"
    r"절반|프로(?!그램|젝트|필|세스)")

# direct 답이 안전장치에 걸렸거나 Qwen을 쓸 수 없을 때의 코드 고정 문장
DIRECT_FALLBACK = ("국가데이터처(KOSIS) 통계표를 찾아 숫자로 답해 드리는 챗봇이에요. "
                   "지표·지역·기간을 넣어 물어봐 주세요(예: 서울 출생아 수 추이).")


class JudgeReply(BaseModel):
    """Qwen 판단의 출력 (JSON 한 줄)."""

    route: Route = Field(description="통계 정보(숫자·지표·지역·기간·표)를 묻거나 그럴 수 있으면 rag, "
                                     "인사·감사·사용법·기능 질문처럼 통계 정보를 묻지 않으면 direct.")
    reply: str | None = Field(default=None, description="route가 direct일 때 사용자에게 보낼 짧은 답. 숫자를 쓰지 않는다. "
                                                        "통계 사실을 말하지 않는다. rag면 null.")


JudgeLLM = Callable[[str], JudgeReply]  # 이번 질문 -> JudgeReply

SYSTEM = (
    "너는 국가통계(KOSIS) 챗봇의 첫 판단 단계다. 이 챗봇은 KOSIS 통계표를 찾아, 표의 원자료로 코드가 계산한 숫자로 답하고 "
    "차트를 그려 준다. 사용자 메시지를 보고 route를 고른다.\n"
    "- rag: 통계 정보(숫자, 지표, 지역, 기간, 통계표)를 묻거나, 묻는 것일 수 있는 메시지. 애매하면 rag.\n"
    "- direct: 인사, 감사, 작별, 사용법, 이 챗봇이 무엇을 할 수 있는지처럼 통계 정보를 묻지 않는 메시지.\n"
    "direct면 reply에 사용자 말투에 맞춘 짧은 답(두세 문장)을 쓴다. reply에는 숫자를 절대 쓰지 않고, 통계 사실이나 "
    "수치를 말하지 않는다. 필요하면 지표·지역·기간을 넣어 물어보라고 안내한다.\n"
    "다른 말 없이 JSON 객체 한 줄로만 답하라. 키: route (\"rag\" | \"direct\"), reply (문자열 또는 null)\n\n"
    "예시:\n"
    "메시지: 안녕하세요\n답: {\"route\": \"direct\", \"reply\": \"안녕하세요! 궁금한 통계가 있으면 지표와 지역, 기간을 넣어 물어봐 주세요.\"}\n"
    "메시지: 여기서 뭘 할 수 있어?\n답: {\"route\": \"direct\", \"reply\": \"KOSIS 통계표를 찾아 숫자로 답하고 차트도 그려 드려요. "
    "예를 들어 지역별 출생아 수나 고령인구 비율을 물어보세요.\"}\n"
    "메시지: 요즘 결혼 많이 해?\n답: {\"route\": \"rag\", \"reply\": null}\n"
    "메시지: 비트코인 가격 알려줘\n답: {\"route\": \"rag\", \"reply\": null}"
)


def parse_judge_reply(text: str) -> JudgeReply:
    """응답의 첫 JSON 객체를 JudgeReply로 검증한다(앞뒤 말·코드 블록·생각 단계 <think>…</think>는 무시)."""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    start = text.find("{")
    if start < 0:
        raise ValueError(f"no JSON in reply: {text[:80]!r}")
    data, _ = json.JSONDecoder().raw_decode(text, start)
    return JudgeReply.model_validate(data)


def make_judge_llm(chat_model) -> JudgeLLM:
    """LangChain chat model(Qwen, OpenAI 호환 서버) → JudgeLLM. 호출 1회, JSON 한 줄(기존 해석기와 같은 방식)."""

    def ask(question: str) -> JudgeReply:
        reply = chat_model.invoke([("system", SYSTEM), ("human", f"메시지: {question}")])
        return parse_judge_reply(str(reply.content))

    return ask


def asks_for_stats(parsed: ParsedQuery) -> bool:
    """실전 2 규칙이 통계 조건(지표·시도·기간·분류)을 하나라도 읽었는가."""
    return bool(parsed.measures or parsed.regions or parsed.years or parsed.period_start or parsed.period_end
                or parsed.dimensions or parsed.recent)


def route_by_rules(question: str, parsed: ParsedQuery) -> Route | None:
    """규칙 경로: rag | direct | None(판단 보류)."""
    if asks_for_stats(parsed):
        return "rag"
    if SMALL_TALK.search(question) and not REST.findall(POLITE.sub(" ", SMALL_TALK.sub(" ", question))):
        return "direct"
    return None


def safe_reply(reply: str | None, parse: Callable[[str], ParsedQuery] | None = None) -> str:
    """direct 답의 안전장치: 비었거나, 숫자가 있거나, 실전 2 규칙이 지표·시도를 읽으면 코드 고정 문장."""
    text = (reply or "").strip()
    if not text or NUMBERS.search(text):
        return DIRECT_FALLBACK
    if parse is not None:
        parsed = parse(text)
        if parsed.measures or parsed.regions:
            return DIRECT_FALLBACK
    return text


def decide_route(question: str, parsed: ParsedQuery, judge_llm: JudgeLLM | None, *,
                 parse: Callable[[str], ParsedQuery] | None = None,
                 in_conversation: bool = False) -> tuple[Route, str | None, str]:
    """(route, direct 답 또는 None, 누가 정했나 rule | llm | none | llm_failed). Qwen 장애·형식 오류는 rag로 돌아간다.

    in_conversation = 직전 표가 있는 대화. 이때 Qwen은 rag만 정할 수 있다(direct는 규칙 direct만).
    """
    by_rule = route_by_rules(question, parsed)
    if by_rule == "rag":
        return "rag", None, "rule"
    fallback = ("direct", DIRECT_FALLBACK, "rule") if by_rule == "direct" else ("rag", None, "none")
    if judge_llm is None:  # Qwen 없음: 판단 보류는 검색, 규칙 direct는 고정 문장
        return fallback
    if by_rule is None and in_conversation:  # 대화 중 판단 보류 = 후속 질문일 수 있음 → Qwen에 묻지 않고 검색
        return "rag", None, "none"
    try:
        got = judge_llm(question)
    except Exception as e:  # noqa: BLE001 - 판단 실패는 검색으로(지어내지 않음)
        log.warning("judge LLM failed, falling back: %s: %s", type(e).__name__, e)
        route, text, _ = fallback
        return route, text, "llm_failed"
    if by_rule == "direct":  # 규칙이 direct로 정함: Qwen은 문장만 쓴다
        return "direct", safe_reply(got.reply, parse), "rule"
    if got.route == "direct":
        return "direct", safe_reply(got.reply, parse), "llm"
    return "rag", None, "llm"


def make_judge(parse: Callable[[str], ParsedQuery], *, interpreter: Mode = "hybrid", llm: LLMReader | None = None,
               splitter: SplitReader | None = None, judge_llm: JudgeLLM | None = None,
               prefetch: Callable[[str, RunnableConfig], None] | None = None,
               regions_of: RegionsOf | None = None):
    """상태 → 이번 턴 필드. 이번 턴 상태를 모두 비우는 것도 여기서 한다(턴의 첫 에이전트).

    judge_llm = Qwen 판단(make_judge_llm). None이면 경로 판단은 규칙만이고, 판단 보류 턴은 rag(S1과 같은 동작).
    prefetch = S4 미리 검색(SearchSteps.prefetch). 규칙이 바로 direct로 정한 턴(인사)만 빼고, 판단을 시작할 때 원문으로
        ② 몫을 옆 스레드에서 돌려 둔다. 쓰이지 않으면 턴 끝에 버린다.
    regions_of = 표 키 -> 표가 가진 시도(kosis_rag table_regions). None이면 직전 표의 시도 확인(region_gap)을 하지 않는다.
    """

    def judge(state: dict, config: RunnableConfig) -> dict:
        question = state["messages"][-1].content
        parsed = parse(question)
        if route_by_rules(question, parsed) == "direct" and not chart_request(question)[0]:
            reading = Reading(resolved_question=question, by="rule")  # 0번: 이월 해석 전에 direct
        else:
            if prefetch is not None:
                prefetch(question, config)
            reading = follow_up(question, state.get("active_question"), parse) if state.get("active_table") else None
            if reading is None:
                reading = read_turn(question, active_table=state.get("active_table"),
                                    active_question=state.get("active_question"), parse=parse, mode=interpreter, llm=llm)
            if (reading.use_previous_table and not reading.chart_only
                    and region_gap(reading.resolved_question, state.get("active_table"), parse, regions_of)):
                reading = reading.model_copy(update={"use_previous_table": False})  # 직전 표에 없는 시도: 새로 찾음
        wants_chart = reading.chart_only or reading.chart_type is not None or chart_request(question)[0]
        # 다시 쓴 질문으로 새로 찾는 턴(지표만 바꾼 이월, 직전 표에 없는 시도): ②가 resolved_question으로 찾는다
        search_again = (not reading.use_previous_table and not reading.chart_only
                        and reading.resolved_question != question)
        carried = (reading.use_previous_table or search_again) and not reading.chart_only \
            and reading.resolved_question != question
        route, direct_text, route_by = "rag", None, "rule"
        if not reading.use_previous_table and not wants_chart and not search_again:  # 새 질문 턴만 경로를 판단한다
            route, direct_text, route_by = decide_route(question, parsed, judge_llm, parse=parse,
                                                        in_conversation=bool(state.get("active_table")))
        split = None
        if route == "rag" and not reading.use_previous_table and not search_again:
            split = split_question(question, parse, splitter)
        subs = split.sub_questions if split and len(split.sub_questions) > 1 else []
        return {
            # ① 판단 결과
            "route": route, "direct_text": direct_text, "route_by": route_by,
            "question": question, "resolved_question": reading.resolved_question,
            "interpreted_by": reading.by, "reused_table": reading.use_previous_table, "carried_over": carried,
            "chart_requested": wants_chart, "chart_type": reading.chart_type, "chart_only": reading.chart_only,
            "sub_questions": subs, "combine_op": split.op if subs else None, "split_by": split.by if subs else None,
            # 이번 턴 상태 비우기 (②~⑤가 채움)
            "fallback_searched": False, "chart_note": None, "attempts": [], "stat": None, "table_key": None,
            "chart": None, "outcome": None, "degraded": False, "sub_results": None, "combined": None,
            "plan_key": None, "search_questions": None, "calc_question": None, "sub_calc_questions": None, "sub_searches": None, "sub_keys": None, "timings": {},
        }

    return judge
