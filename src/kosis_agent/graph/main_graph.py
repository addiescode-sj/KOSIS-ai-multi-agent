"""메인 그래프 (T3-03 옵션 B: 단계별 ReAct 판단). 단계 경계마다 Thought → Action을 한 번 정하고(graph/react.py),
조건부 경로는 그 Action 값만 읽는다. Observation은 실제 툴 결과다.

    START → understand ─(template: 인사·통계 무관)──────────────────────────────────────→ finalize
                ├─(clarify: 무엇을 찾는지 모름 → 다음 턴 답을 받아 rewrite)───────────────→ finalize
                ├─(rewrite: 되묻기 답 · 다듬어야 검색 가능)→ rewrite ⟲(불충분 · 1회) ─(2회 불충분)→ finalize(clarify)
                │                                              └─(충분)→ search
                ├─(chart_only: "그 표로 막대그래프")→ chart ─────────────────────────────────→ finalize
                ├─(multi: 하위 질문 둘 이상)→ multi ─(같은 표)→ chart → review → finalize
                │                                    └─(표가 다름)────────────────────────→ finalize
                ├─(reuse: 지시어·이월)→ statistics
                └─(search)→ search ─(gate 거절 no_item · 재작성 전 · 판단 켜짐)→ rewrite → search (1회)
                               ├─(gate 거절 · 넘길 표 없음 = 답할 수 없음)→ finalize (툴 추가 호출 없음)
                               └─(답할 수 있음)→ statistics ─(답함)→ chart → review → finalize
                                                   ├─(표 불일치 · 예산 남음)→ statistics
                                                   ├─(이월 턴, 직전 표 표 불일치)→ search(다시 쓴 문장)
                                                   └─(되묻기 · 거절 · 예산 끝)→ finalize
    finalize → END   (outcome: answered | refused | clarify | failed)

노드별 역할:
- understand 이번 턴 상태를 비우고 질의를 이해한다. 매 턴 Qwen이 먼저 판단한다(Judge.understand, 규칙 판단 없음).
             search·rewrite면 그다음 직전 표 재사용(read_turn 이월·지시어)과 분해(split_question)를 본다. 되묻기 다음 턴이면 rewrite로 보낸다(pending_clarify).
             옵션 B면 규칙이 나눈 멀티쿼리를 Qwen이 한 번 검토한다(Judge.split: 같은 지표 비교면 합침).
- rewrite    검색에 쓸 한 문장으로 다시 쓴다(Judge.rewrite, 원문·이전 문장 낱말만 → grounded). 옵션 B면 Qwen이 충분성을
             판단해(Judge.sufficiency) 불충분하면 1회 더 쓴다. 판단 LLM이 없으면 되묻기 답을 원 질문 뒤에 붙인 문장을 쓴다.
- search     search_tables 툴(실전 2 검색 그래프 전체, 2차 리랭커·gate 포함). 새 질문은 사용자 원문으로, 재작성·이월 턴만
             다시 쓴 문장으로 찾는다. 2차 리랭커·gate 결과(route·picked)가 "답할 수 있음/없음" 검수다(LLM 추가 호출 없음).
- statistics summarize_stat 툴(실전 1). 숫자와 답 문장은 실전 1이 만든다.
- multi      멀티쿼리 턴(T3-28).
- chart      make_chart 툴(차트 요청 턴) 또는 실전 1 ChartSpec → Vega-Lite. 답변 다음 순차(Decision: fan-out 안 함).
- review     코드 검수: 출처 줄(chatTemplate = 실전 1 답 + `출처: KOSIS / 조사명 / 통계표명`)이 없으면 코드가 붙이고,
             실전 1 숫자 검증(numbers_verified)과 요청한 차트가 그려졌는지 기록한다. 툴을 부르지 않는다.
- finalize   outcome과 답 메시지를 정한다. 답 문장은 실전 1·2 문장 또는 코드 고정 문장만 쓴다(LLM 문장 생성 없음).

노드는 실전 1·2를 직접 부르지 않고 LangChain 툴을 거친다(`execute_tool <name>` span = agent 계층 채점 근거).
판단 단계(understand·rewrite·review)는 툴이 아니다. 툴로 만들면 agent 채점이 extra_call로 감점한다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph

from kosis_agent.graph.context import LLMReader, Mode, chart_request, read_turn
from kosis_agent.graph.multiquery import SplitReader, split_question
from kosis_agent.graph.react import (TEMPLATES, Judge, Steps, UnderstandStep, clarify_text, grounded, safe)
from kosis_agent.graph.state import AgentState
from kosis_agent.retrieval.search import make_search_tables_tool
from kosis_agent.services.chart import CHART_NAMES, make_chart_tool, to_vega_lite
from kosis_agent.services.combine import combine_text, make_combine_values_tool
from kosis_agent.services.statistics import make_summarize_stat_tool
from kosis_chat.api import summarize_stat_run
from kosis_chat.schemas.io import StatOutput
from kosis_chat.schemas.resources import LLMConfig as StatLLMConfig
from kosis_rag.parse.node import parse_query
from kosis_rag.parse.schemas import ParsedQuery
from kosis_rag.schemas.io import SearchOutput

# summarize_stat의 조건 인자로 넘기는 ParsedQuery 필드 (= evals/schemas.ARG_FIELDS 중 summarize_stat에 해당하는 것)
CONDITION_FIELDS = ("period_start", "period_end", "regions", "dimensions")

# 실전 1이 이 이유로 거절하면 "질문은 괜찮은데 이 표가 맞지 않는다"는 뜻이라 다음 후보 표를 시도해 볼 만하다.
#   no_period(요청 기간이 표에 없음) · no_item(요청 항목이 표에 없음) · class_mismatch(분류가 그 수준·시점에 없음)
#   · out_of_scope(표 범위 밖. 표 키가 tables.csv에 없을 때도 이 이유: services/statistics.py)
# 나머지 이유(causal: 인과 질문, unit_mismatch, not_aggregatable, missing_value: 셀이 '-'·'X')는 질문이나 셀 자체의
# 문제라서, 표를 바꿔도 같은 답이 나오거나 다른 정의의 숫자를 섞게 된다. 그래서 다시 시도하지 않는다.
TABLE_MISMATCH = frozenset({"no_period", "no_item", "class_mismatch", "out_of_scope"})

# 실전 2 gate가 이 이유로 거절하면 재작성 후 1회 다시 검색한다. out_of_scope(주제가 색인 밖)는 다시 써도 범위 밖이라
# 재검색하지 않는다(틀린 표를 found로 고르는 위험만 생김, plans/T3-23.md Result).
RESEARCH_REASONS = frozenset({"no_item"})
MAX_REWRITES = 2  # 재작성 1회 + 재시도 1회 (Decision: 모든 재시도 최대 1회)

# 검색은 found인데 실전 1에 넘길 표가 하나도 없을 때의 답 (코드 고정 문장)
NO_TABLE_TEXT = "질문에 맞는 통계표를 고르지 못했어요. 지표·지역·기간을 조금 더 구체적으로 물어봐 주세요."


def call_tool(tool: BaseTool, args: dict, config: RunnableConfig) -> ToolMessage:
    """노드에서 툴을 ToolCall 형태로 부른다.

    ToolCall(id 포함)로 불러야 content_and_artifact 툴이 ToolMessage(content, artifact)를 돌려준다. dict 인자만
    넘기면 artifact가 버려진다. 노드의 config를 넘겨야 이 턴의 콜백(OTel span) 아래에서 툴 span이 열린다.
    """
    call = {"name": tool.name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}
    return tool.invoke(call, config)


def candidate_keys(search: SearchOutput | None) -> list[str]:
    """실전 1에 넘길 수 있는 표 키, 시도할 순서대로: 실전 2가 고른 표(picked) 다음 나머지 후보(candidates), 중복 없이.

    키는 실전 2 결과 안에서만 고른다. 그래프가 표 키를 새로 만들지 않는다(표 ID 보존 원칙).
    """
    if search is None:
        return []
    return list(dict.fromkeys(c.key for c in [*search.picked, *search.candidates]))


def default_parser(search: Any) -> Callable[[str], ParsedQuery]:
    """실전 2 검색이 쓰는 것과 같은 질의 해석 규칙(parse_query + 시군구 사전).

    시군구 사전은 SearchModule.resources.sigungu에 이미 열려 있다(실전 2 parse 노드도 이것을 쓴다). 사전이 없는
    가짜 검색(테스트)에서는 시군구 없이 읽는다(시도 이름·연도·분류는 그대로 읽힘).
    """
    sigungu = getattr(getattr(search, "resources", None), "sigungu", None)
    return lambda question: parse_query(question, sigungu=sigungu)


def step(state: AgentState, name: str, action: str, by: str, thought: str = "") -> list[dict]:
    """이번 턴 trace에 단계 하나를 더한 전체 목록(reducer 없이 노드가 전체 값을 돌려준다: 턴마다 understand가 비움)."""
    return [*(state.get("trace") or []), {"step": name, "action": action, "by": by, "thought": thought}]


def build_graph(search: Any, stat_llm: StatLLMConfig, *, retry_interval: float = 0.0,
                summarize: Callable[..., StatOutput] = summarize_stat_run, checkpointer: Any = None,
                alt_table_budget: int = 0, parse: Callable[[str], ParsedQuery] | None = None,
                interpreter: Mode = "hybrid", llm: LLMReader | None = None, splitter: SplitReader | None = None,
                judge: Judge | None = None, steps: Steps = "b"):
    """메인 그래프를 compile한다.

    In: search = kosis_rag.api.SearchModule (테스트·stat_oracle에서는 .stream(SearchInput)만 있는 가짜);
        stat_llm = 실전 1의 LLMConfig (실전 2 LLMConfig와 다른 클래스·필드);
        retry_interval = 실전 1의 HCX 429 재시도 대기 초; summarize = 실전 1 summarize_stat_run (테스트에서는 가짜);
        checkpointer = LangGraph checkpointer (None이면 턴 사이 상태를 잇지 않음, graph/state.new_checkpointer 권장);
        alt_table_budget = 첫 표가 표 불일치로 거절됐을 때 더 시도할 후보 표 수. 기본 0(끔, plans/T3-23.md Result).
        parse = 질문 → ParsedQuery (None이면 default_parser(search)). summarize_stat의 조건 인자를 채운다.
        interpreter·llm = 이월 해석(graph/context.py). splitter = 멀티쿼리 분해의 LLM 단계(graph/multiquery.py).
        judge = ReAct 단계 판단(graph/react.Judge, Qwen). None이면 판단 단계는 규칙만 쓴다.
        steps = off(판단 LLM 안 씀) | a(이해 + 검색 거절 후 재작성) | b(a + 재작성 충분성 판단 + 분해 검토, 기본).
    Out: AgentState 위의 compiled graph. stream_mode "custom"으로 돌리면 실전 2 노드마다 (노드 이름, trace 항목),
        그다음 실전 1 시도마다 ("stat", {"seconds", "key", "spans"})가 온다.
    Errors: 실전 1의 LLMKeyMissing·RuntimeError와 실전 2의 예외는 그대로 올라간다(SSE `error`). 판단 LLM의 장애는
        규칙 경로로 대신한다(react.safe).
    """
    if alt_table_budget < 0:
        raise ValueError("alt_table_budget cannot be negative")
    search_tables = make_search_tables_tool(search)
    summarize_stat = make_summarize_stat_tool(stat_llm, retry_interval=retry_interval, summarize=summarize)
    make_chart = make_chart_tool()
    combine_values = make_combine_values_tool()
    parse = parse or default_parser(search)
    judge = judge if steps != "off" else None

    def understand_node(state: AgentState) -> dict:
        """Thought → Action: template | clarify | rewrite | search(·reuse·chart·multi). 이번 턴 상태를 비운다."""
        question = state["messages"][-1].content
        fresh = {"question": question, "resolved_question": question, "interpreted_by": "none", "reused_table": False,
                 "carried_over": False, "fallback_searched": False, "chart_requested": chart_request(question)[0],
                 "chart_type": chart_request(question)[1], "chart_only": False, "chart_note": None, "attempts": [],
                 "stat": None, "table_key": None, "chart": None, "outcome": None, "degraded": False,
                 "sub_questions": [], "combine_op": None, "split_by": None, "sub_results": None, "combined": None,
                 "intent": "search", "template": None, "missing": [], "rewrite_source": None, "rewrite_feedback": None,
                 "rewrite_tries": 0, "rewritten": False, "researched": False, "review": None, "answer_text": None,
                 "refusal_reason": None, "pending_clarify": None, "trace": []}
        if pending := state.get("pending_clarify"):  # 되묻기 다음 턴: 원 질문 + 이번 답으로 다시 쓴다
            source = f"원래 질문: {pending}\n되묻기에 대한 답: {question}"
            return {**fresh, "intent": "rewrite", "rewrite_source": source,
                    "trace": [{"step": "understand", "action": "rewrite", "by": "rule", "thought": "되묻기에 대한 답"}]}
        # ReAct가 매 턴 먼저 판단한다(규칙 판단 없음). LLM이 없거나 장애면 search(= ReAct 전 동작, 범위 밖은 gate가 거절)
        got = safe(lambda: judge.understand(question, state.get("active_question"))) if judge else None
        decided, by = (got, "llm") if got is not None else (UnderstandStep(thought="판단 없음 → 검색", action="search"), "none")
        trace = [{"step": "understand", "action": decided.action, "by": by, "thought": decided.thought}]
        if decided.action == "template":
            kind = decided.kind or "not_statistics"
            # 거절 이유는 실전 2 gate와 같은 out_of_scope(색인 밖). 종류는 template에 둔다
            return {**fresh, "intent": "template", "template": kind, "refusal_reason": "out_of_scope", "trace": trace}
        if decided.action == "clarify":
            return {**fresh, "intent": "clarify", "missing": list(decided.missing), "trace": trace}
        # search·rewrite: 직전 표를 다시 쓰는 턴(지시어·이월·차트만, T3-27)인지 본다
        reading = read_turn(question, active_table=state.get("active_table"),
                            active_question=state.get("active_question"), parse=parse, mode=interpreter, llm=llm)
        wants_chart = reading.chart_only or reading.chart_type is not None or chart_request(question)[0]
        # 이월 턴 = 직전 표를 쓰되 질문을 다시 쓴 턴. 지시어 턴("그 표에서 …")은 원문을 그대로 쓴다.
        carried = reading.use_previous_table and not reading.chart_only and reading.resolved_question != question
        base = {**fresh, "resolved_question": reading.resolved_question, "interpreted_by": reading.by,
                "reused_table": reading.use_previous_table, "carried_over": carried, "chart_requested": wants_chart,
                "chart_type": reading.chart_type, "chart_only": reading.chart_only}
        if reading.use_previous_table:
            action = "chart_only" if reading.chart_only else "reuse"
            trace.append({"step": "context", "action": action, "by": reading.by, "thought": "직전 표를 다시 씀"})
            return {**base, "intent": "search", "trace": trace}
        if decided.action == "rewrite":
            return {**base, "intent": "rewrite", "rewrite_source": f"질문: {question}", "trace": trace}
        split = split_question(question, parse, splitter)
        subs = split.sub_questions if len(split.sub_questions) > 1 else []
        if subs and split.by == "rule" and steps == "b" and judge is not None:
            # 규칙 분해 오류는 대부분 같은 표 비교를 과분해한 것(plans/T3-46.md: rag → rewrite 14)이라 한 번 검토한다
            review = safe(lambda: judge.split(question, subs))
            if review is not None:
                trace.append({"step": "split", "action": review.action, "by": "llm", "thought": review.thought})
                if review.action == "merge":
                    subs = []
        return {**base, "sub_questions": subs, "combine_op": split.op if subs else None,
                "split_by": split.by if subs else None, "trace": trace}

    def after_understand(state: AgentState) -> str:
        if state["intent"] in ("template", "clarify"):
            return "finalize"
        if state["intent"] == "rewrite":
            return "rewrite"
        if state["chart_only"]:
            return "chart"
        if state.get("sub_questions"):
            return "multi"
        return "statistics" if state["reused_table"] else "search"

    def rewrite_node(state: AgentState) -> dict:
        """Action Input = 검색에 쓸 한 문장. 원문·이전 문장 낱말만(grounded). 옵션 B면 충분성을 판단한다."""
        source, feedback = state["rewrite_source"], state.get("rewrite_feedback")
        tries = state.get("rewrite_tries", 0) + 1
        fallback = source.split("\n")[-1].split(": ", 1)[-1]  # LLM 없이: 되묻기 답이면 원 질문 + 답, 아니면 원문
        if "되묻기에 대한 답" in source:
            fallback = " ".join(line.split(": ", 1)[-1] for line in source.split("\n"))
        got = safe(lambda: judge.rewrite(source, feedback)) if judge else None
        if got is not None and grounded(got.question, source, parse):
            question, by, thought = got.question, "llm", got.thought
        else:
            question, by, thought = fallback, "rule", "원문(·되묻기 답)을 그대로 이어 씀"
        trace = step(state, "rewrite", "rewrite", by, thought)
        update = {"resolved_question": question, "rewritten": True, "rewrite_tries": tries}
        verdict = safe(lambda: judge.sufficiency(question)) if (judge and steps == "b") else None
        if verdict is None or verdict.action == "sufficient":
            trace.append({"step": "sufficiency", "action": "sufficient", "by": "llm" if verdict else "none",
                          "thought": verdict.thought if verdict else ""})
            return {**update, "intent": "search", "trace": trace}
        trace.append({"step": "sufficiency", "action": "retry", "by": "llm", "thought": verdict.thought})
        return {**update, "intent": "rewrite", "missing": list(verdict.missing),
                "rewrite_feedback": f"불충분: {verdict.thought}", "trace": trace}

    def after_rewrite(state: AgentState) -> str:
        if state["intent"] == "search":
            return "search"
        return "rewrite" if state["rewrite_tries"] < MAX_REWRITES else "finalize"

    def search_node(state: AgentState, config: RunnableConfig) -> dict:
        """표를 찾는다. 2차 리랭커·gate 결과가 "답할 수 있음/없음" 검수다. 2차 리랭커가 실패했으면 degraded.

        - 새 질문: 사용자 원문으로 찾는다(LLM의 영향을 재작성·이월 턴으로 좁힘).
        - 재작성 턴: rewrite가 만든 문장(grounded)으로 찾는다.
        - 이월 턴인데 직전 표가 표 불일치로 거절함(after_statistics → 여기): 다시 쓴 독립 질문으로 찾는다.
        """
        fallback = bool(state.get("carried_over") and state["attempts"])
        question = state["resolved_question"] if (fallback or state.get("rewritten")) else state["question"]
        out: SearchOutput = call_tool(search_tables, {"question": question}, config).artifact
        degraded = any(s.get("node") == "rerank2" and s.get("stage2_error") for s in out.spans)
        answerable = out.route == "found" and bool(candidate_keys(out))
        thought = (f"표 {len(out.picked)}개 선택: " + ", ".join(t.key for t in out.picked[:3]) if answerable
                   else f"gate 거절: {out.refusal_reason or '넘길 표 없음'}")
        update = {"search": out, "degraded": degraded, "refusal_reason": out.refusal_reason,
                  "researched": bool(state.get("researched") or state.get("rewritten")),
                  "trace": step(state, "verify", "answerable" if answerable else "unanswerable", "rerank2", thought)}
        if fallback:  # 이제부터 직전 표가 아니라 새 검색 후보에서 고른다(이미 시도한 직전 표는 next_table이 건너뜀)
            update.update(reused_table=False, fallback_searched=True)
        return update

    def after_search(state: AgentState) -> str:
        out = state["search"]
        if out.route == "found" and next_table(state) is not None:
            return "statistics"
        if (out.route == "refuse" and out.refusal_reason in RESEARCH_REASONS and judge is not None
                and not state.get("researched") and not state.get("carried_over")):
            return "rewrite_after_search"  # Observation(거절 이유)을 보고 1회만 다시 쓴다
        return "finalize"  # 답할 수 없음: 툴을 더 부르지 않고 끝낸다

    def research_node(state: AgentState) -> dict:
        """검색 거절 Observation을 재작성 입력으로 넘긴다(재작성 → 재검색 1회)."""
        out = state["search"]
        names = ", ".join(t.table_name for t in out.candidates[:3]) or "없음"
        return {"rewrite_source": f"질문: {state['question']}", "rewrite_tries": 0, "researched": True,
                "rewrite_feedback": f"검색 결과 맞는 표 없음({out.refusal_reason}). 가까운 후보: {names}"}

    def next_table(state: AgentState) -> str | None:
        """다음에 실전 1에 넘길 표 키. 지시어 턴은 직전 표 하나만 쓴다(사용자가 가리킨 표를 몰래 바꾸지 않음)."""
        tried = {a["key"] for a in state["attempts"]}
        if state["reused_table"]:
            return None if tried else state["active_table"]
        return next((k for k in candidate_keys(state["search"]) if k not in tried), None)

    def ask(question: str, key: str, config: RunnableConfig) -> StatOutput:
        """summarize_stat 툴 한 번: 질문 + 표 키 + 그 질문을 실전 2 규칙으로 읽은 조건 4개."""
        parsed = parse(question)
        args = {"question": question, "table_key": key,
                **{field: getattr(parsed, field) for field in CONDITION_FIELDS}}
        return call_tool(summarize_stat, args, config).artifact

    def multi_node(state: AgentState, config: RunnableConfig) -> dict:
        """멀티쿼리 턴: 하위 질문마다 검색하고, 표가 다르면 하위 질문마다 실전 1에 묻는다 (T3-28과 같음).

        - 같은 표 되돌림: 하위 질문이 모두 같은 표를 찾으면 나누지 않은 것으로 보고 원문 한 번으로 묻는다.
        - 하위 질문 하나가 검색 거절·실전 1 거절이면 나머지는 답하고, 그 하위 질문에는 거절 문장을 붙인다(지어내지 않음).
        """
        subs = state["sub_questions"]
        searches: list[SearchOutput] = [call_tool(search_tables, {"question": q}, config).artifact for q in subs]
        keys = [(candidate_keys(o) or [None])[0] if o.route == "found" else None for o in searches]
        degraded = any(s.get("node") == "rerank2" and s.get("stage2_error") for o in searches for s in o.spans)
        base = {"search": searches[-1], "degraded": degraded,
                "trace": step(state, "verify", "answerable" if any(keys) else "unanswerable", "rerank2",
                              f"하위 질문 {len(subs)}개의 표: {keys}")}
        if keys[0] is not None and len(set(keys)) == 1:  # 같은 표: 원문 한 번
            stat = ask(state["question"], keys[0], config)
            answer = stat.answer
            return {**base, "sub_questions": [], "stat": stat, "table_key": keys[0],
                    "attempts": [{"key": keys[0], "outcome": answer.outcome,
                                  "reason": answer.refusal.reason if answer.refusal else None}]}
        results, attempts, last = [], [], None
        for question, out, key in zip(subs, searches, keys):
            if key is None:
                text = (out.refusal_text or NO_TABLE_TEXT) if out.route == "refuse" else NO_TABLE_TEXT
                results.append({"question": question, "table_key": None, "outcome": "refused",
                                "reason": out.refusal_reason, "text": text, "answer": None})
                continue
            stat = ask(question, key, config)
            answer = stat.answer
            reason = answer.refusal.reason if answer.refusal else None
            attempts.append({"key": key, "outcome": answer.outcome, "reason": reason})
            results.append({"question": question, "table_key": key, "outcome": answer.outcome, "reason": reason,
                            "text": answer.text, "answer": answer.model_dump(mode="json")})
            if answer.outcome == "answered":
                last = (stat, key, question)
        combined = None
        if state.get("combine_op") and len(results) == 2 and all(r["outcome"] == "answered" for r in results):
            args = {"op": state["combine_op"], "answers": [r["answer"] for r in results]}
            combined = call_tool(combine_values, args, config).artifact
        update = {**base, "sub_results": results, "combined": combined, "attempts": attempts}
        if last is not None:  # 다음 턴의 "그 표"는 마지막으로 답한 하위 질문의 표 (plans/T3-28.md 한계)
            update.update(stat=last[0], table_key=last[1], resolved_question=last[2])
        return update

    def after_multi(state: AgentState) -> str:
        if state.get("sub_results") is None:  # 같은 표로 되돌린 턴 = 한 턴 질문과 같은 경로
            return "chart" if state["attempts"][-1]["outcome"] == "answered" else "finalize"
        return "finalize"  # ponytail: 멀티쿼리 답에는 차트를 그리지 않는다. 하위 답마다 차트가 필요해지면 추가

    def statistics_node(state: AgentState, config: RunnableConfig) -> dict:
        """표 하나로 실전 1에 답을 받는다. 숫자와 답 문장은 실전 1의 코드가 만든다.

        질문은 해석된 문장(resolved_question): 이월 턴이면 조건을 채운 독립 질문, 재작성 턴이면 다시 쓴 문장.
        """
        key = next_table(state)
        question = state.get("resolved_question") or state["question"]
        stat = ask(question, key, config)
        answer = stat.answer
        reason = answer.refusal.reason if answer.refusal else None
        attempts = [*state["attempts"], {"key": key, "outcome": answer.outcome, "reason": reason}]
        # 턴은 마지막으로 시도한 표에서 끝난다. 평가 하네스도 "마지막 summarize_stat 호출"을 이 턴의 표·답으로 채점한다.
        return {"attempts": attempts, "stat": stat, "table_key": key,
                "trace": step(state, "answer", answer.outcome, "phase1", reason or "")}

    def after_statistics(state: AgentState) -> str:
        last = state["attempts"][-1]
        if last["outcome"] == "answered":
            return "chart"
        mismatch = last["outcome"] == "refused" and last["reason"] in TABLE_MISMATCH
        if mismatch and state.get("carried_over") and not state.get("fallback_searched"):
            return "search"  # 이월 턴: 직전 표에 그 지역·기간이 없음 → 다시 쓴 문장으로 검색 (search_node)
        retry = mismatch and len(state["attempts"]) <= alt_table_budget and next_table(state) is not None
        return "statistics" if retry else "finalize"

    def chart_node(state: AgentState, config: RunnableConfig) -> dict:
        """차트를 요청했으면 make_chart 툴(요청 종류), 아니면 실전 1이 준 차트를 그대로 그린다."""
        if state["chart_only"]:  # 숫자는 직전 답 그대로, 차트만 다시
            spec, key = state.get("active_chart"), state.get("active_table")
        else:
            spec, key = state["stat"].chart, state["table_key"]
        if not state["chart_requested"]:
            return {"chart": to_vega_lite(spec)}
        args = {"table_key": key, "chart": spec.model_dump(mode="json") if spec else None,
                "chart_type": state.get("chart_type")}
        out = call_tool(make_chart, args, config).artifact
        return {"chart": out["vega_lite"], "chart_note": out["note"]}

    def after_chart(state: AgentState) -> str:
        return "finalize" if state["chart_only"] else "review"

    def review_node(state: AgentState) -> dict:
        """코드 검수(툴 없음): 답이 chatTemplate(실전 1 답 + 출처 줄)을 따르는가, 요청한 차트가 그려졌는가."""
        answer = state["stat"].answer
        text, checks = answer.text, {"numbers_verified": answer.numbers_verified}
        source = answer.source
        cited = source is not None and source.survey_nm in text and source.tbl_nm in text
        checks["source"] = cited
        if source is not None and not cited:  # 출처는 코드가 만든 citation()을 붙인다(LLM 아님)
            text = f"{text}\n\n출처: {source.citation()}"
        # 시각화할 수 없는 경우(차트 데이터 없음)는 통과로 본다. 요청했는데 못 그렸으면 chart_note가 이유를 말한다.
        checks["chart"] = state.get("chart") is not None or state["stat"].chart is None
        ok = checks["source"] and checks["chart"]
        thought = "" if ok else ", ".join(k for k in ("source", "chart") if not checks[k]) + " 보완"
        return {"answer_text": text, "review": checks, "trace": step(state, "review", "pass" if ok else "fixed", "code", thought)}

    def finalize_node(state: AgentState) -> dict:
        """outcome과 답 메시지를 정한다. 답한 표는 다음 턴의 "그 표"(active_table)가 되고, 해석된 질문과 차트 데이터는
        다음 턴의 이월·차트 요청에 쓰인다(active_question · active_chart)."""
        if state.get("intent") == "template":
            return {"outcome": "refused", "messages": [AIMessage(TEMPLATES[state["template"]])]}
        if state.get("intent") in ("clarify", "rewrite"):  # 이해 단계 되묻기 · 재작성 2회 불충분
            return {"outcome": "clarify", "pending_clarify": state["question"],
                    "messages": [AIMessage(clarify_text(state.get("missing") or []))]}
        stat: StatOutput | None = state.get("stat")
        if (results := state.get("sub_results")) is not None:  # 멀티쿼리 턴: 하위 답을 순서대로 잇는다
            parts = [r["text"] for r in results]
            if (combined := state.get("combined")) is not None:
                parts.append(combine_text(combined))
            answered = [r for r in results if r["outcome"] == "answered"]
            outcome = "answered" if answered else results[0]["outcome"]
            update = {"outcome": outcome, "messages": [AIMessage("\n\n".join(parts))]}
            if stat is not None:
                update.update(active_table=state["table_key"], active_question=state["resolved_question"],
                              active_chart=stat.chart)
            return update
        if state["chart_only"]:  # 차트만 다시 그린 턴: 대화 상태(표·질문·차트)는 그대로 둔다
            if state.get("chart") is None:
                return {"outcome": "failed", "messages": [AIMessage(state.get("chart_note") or "")]}
            name = CHART_NAMES.get(state.get("chart_type") or "", "차트")
            text = f"직전 답을 {name}로 다시 그렸어요." + (f" {state['chart_note']}" if state.get("chart_note") else "")
            return {"outcome": "answered", "messages": [AIMessage(text)]}
        if stat is None:  # 실전 1까지 가지 못함: 검색 거절, 또는 넘길 표 없음
            out: SearchOutput = state["search"]
            if out.route == "refuse":
                return {"outcome": "refused", "messages": [AIMessage(out.refusal_text or "")]}
            return {"outcome": "failed", "messages": [AIMessage(NO_TABLE_TEXT)]}
        return {"outcome": stat.answer.outcome, "active_table": state["table_key"],
                "active_question": state.get("resolved_question") or state["question"],
                "active_chart": stat.chart if stat.chart is not None else None,
                "messages": [AIMessage(state.get("answer_text") or stat.answer.text)]}

    graph = StateGraph(AgentState)
    graph.add_node("understand", understand_node)
    graph.add_node("rewrite", rewrite_node)
    graph.add_node("search", search_node)
    graph.add_node("rewrite_after_search", research_node)
    graph.add_node("statistics", statistics_node)
    graph.add_node("multi", multi_node)
    graph.add_node("chart", chart_node)
    graph.add_node("review", review_node)
    graph.add_node("finalize", finalize_node)
    graph.add_edge(START, "understand")
    graph.add_conditional_edges("understand", after_understand,
                                ["finalize", "rewrite", "chart", "multi", "statistics", "search"])
    graph.add_conditional_edges("rewrite", after_rewrite, ["search", "rewrite", "finalize"])
    graph.add_conditional_edges("search", after_search, ["statistics", "rewrite_after_search", "finalize"])
    graph.add_edge("rewrite_after_search", "rewrite")
    graph.add_conditional_edges("multi", after_multi, ["chart", "finalize"])
    graph.add_conditional_edges("statistics", after_statistics, ["statistics", "search", "chart", "finalize"])
    graph.add_conditional_edges("chart", after_chart, ["review", "finalize"])
    graph.add_edge("review", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
