"""메인 그래프 (T3-23 결정적 워크플로 + T3-27 이월·차트 요청). 다음에 무엇을 할지는 아래 조건부 경로(코드)가 정한다.

    START → context ─(차트만 다시: "그 표로 막대그래프")──────────────────────────────┐
                ├─(하위 질문 둘 이상: "출생아 수와 외국인주민 수")→ multi ─(같은 표)→ chart ─→ finalize
                │                                                       └─(표가 다름)→ finalize
                ├─(직전 표 재사용: 지시어 "그 표에서…", 이월 "부산은?"·"아니 2023년으로")─┐   │
                └─(새 질문)→ search ─(refuse · 넘길 표 없음)→ finalize                 │   │
                                └─(found)→ statistics ←───────────────────────────────┘   │
                                             ├─(표 불일치 거절 · 예산 남음)→ statistics      │
                                             ├─(이월 턴인데 직전 표가 표 불일치로 거절)→ search(다시 쓴 문장)
                                             ├─(답함)→ chart → finalize ←──────────────────┘
                                             └─(되묻기 · 질문 문제 · 예산 끝)→ finalize
    finalize → END   (outcome: answered | refused | clarify | failed)

노드별 역할:
- context    이번 턴 상태를 비우고, 이번 질문을 직전 대화에 비춰 해석한다(graph/context.py read_turn):
             직전 표를 다시 쓸지, 실전 1에 넘길 문장(이월이면 조건을 채운 독립 질문), 차트 요청.
             규칙이 먼저 판단하고, 규칙이 판단하지 못한 턴만 LLM 한 번(interpreter="hybrid", LLM이 있을 때).
- search     search_tables 툴(실전 2 검색 그래프 전체)을 사용자 원문으로 부른다.
- statistics summarize_stat 툴(실전 1)을 표 하나로 부른다. 표 불일치로 거절되면 다음 후보 표로 다시 들어온다.
- multi      멀티쿼리 턴(T3-28): 하위 질문마다 search_tables → summarize_stat을 부른다. 모두 같은 표를 찾으면 원문
             한 번으로 실전 1에 묻는다(같은 표 비교는 실전 1 compare가 답함). 계산 요청이면 combine_values 툴(코드 계산).
- chart      사용자가 차트를 요청했으면 make_chart 툴로 요청한 종류를 그리고, 아니면 실전 1이 준 ChartSpec을
             그대로 Vega-Lite로 바꾼다(툴 아님: 요청하지 않은 툴 호출은 하네스가 extra_call로 감점).
- finalize   outcome과 답 메시지를 정한다. 답 문장은 실전 1·2가 만든 것이나 코드 고정 문장만 쓴다(LLM 문장 생성 없음).

노드는 실전 1·2를 직접 부르지 않고 LangChain 툴을 거친다. 그래서 `execute_tool <name>` span이 생기고, T3-06 agent
계층이 툴 이름·인자·결과로 그래프를 채점할 수 있다(plans/agent-graph-refactor.md P2). 실전 1·2 그래프는 각 툴 안에서
통째로 돈다. 상태 스키마가 서로 다른 그래프를 노드 안에서 부르는 것은 LangGraph가 안내하는 방식이다.
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


def build_graph(search: Any, stat_llm: StatLLMConfig, *, retry_interval: float = 0.0,
                summarize: Callable[..., StatOutput] = summarize_stat_run, checkpointer: Any = None,
                alt_table_budget: int = 0, parse: Callable[[str], ParsedQuery] | None = None,
                interpreter: Mode = "hybrid", llm: LLMReader | None = None, splitter: SplitReader | None = None):
    """메인 그래프를 compile한다.

    In: search = kosis_rag.api.SearchModule (테스트·stat_oracle에서는 .stream(SearchInput)만 있는 가짜);
        stat_llm = 실전 1의 LLMConfig (실전 2 LLMConfig와 다른 클래스·필드);
        retry_interval = 실전 1의 HCX 429 재시도 대기 초; summarize = 실전 1 summarize_stat_run (테스트에서는 가짜);
        checkpointer = LangGraph checkpointer (None이면 턴 사이 상태를 잇지 않음, graph/state.new_checkpointer 권장);
        alt_table_budget = 첫 표가 표 불일치로 거절됐을 때 더 시도할 후보 표 수. 기본 0(끔) = 1단계와 같은 경로.
            기본을 끈 이유: T3-22 실제 검색 순위를 재생한 측정(plans/T3-23.md Result)에서 재시도 2건 중 1건이
            정답 표가 아닌 표로 "답함"이 됐다(거절 → 틀린 표의 숫자). 데이터 정확성이 먼저라서, HCX·실시간 검색으로
            충분한 표본을 잰 뒤에 켤지 정한다.
        parse = 질문 → ParsedQuery (None이면 default_parser(search)). summarize_stat의 조건 인자를 채운다.
        interpreter = 이월 해석 방식 rules | hybrid | llm (graph/context.py). llm = LLMReader(make_llm_reader로 만듦).
            llm이 None이면 hybrid는 rules와 같다(LLM 단계 없음).
        splitter = 멀티쿼리 분해의 LLM 단계(graph/multiquery.py make_llm_splitter). 규칙이 판단 보류한 턴만 부른다.
            None이면 규칙으로만 나눈다.
    Out: AgentState 위의 compiled graph. stream_mode "custom"으로 돌리면 실전 2 노드마다 (노드 이름, trace 항목),
        그다음 실전 1 시도마다 ("stat", {"seconds", "key", "spans"})가 온다.
    Errors: 실전 1의 LLMKeyMissing·RuntimeError와 실전 2의 예외는 그대로 올라간다(SSE `error`). 실전 1의
        LookupError(표 키가 tables.csv에 없음)는 summarize_stat 툴이 out_of_scope 거절로 바꾸므로, 다음 후보 표로 넘어간다.
    """
    if alt_table_budget < 0:
        raise ValueError("alt_table_budget cannot be negative")
    search_tables = make_search_tables_tool(search)
    summarize_stat = make_summarize_stat_tool(stat_llm, retry_interval=retry_interval, summarize=summarize)
    make_chart = make_chart_tool()
    combine_values = make_combine_values_tool()
    parse = parse or default_parser(search)

    def context_node(state: AgentState) -> dict:
        """이번 턴 상태를 비우고, 이번 질문을 해석한다(직전 표 재사용 · 실전 1에 넘길 문장 · 차트 요청)."""
        question = state["messages"][-1].content
        reading = read_turn(question, active_table=state.get("active_table"),
                            active_question=state.get("active_question"), parse=parse, mode=interpreter, llm=llm)
        # 종류 말 없이 "그래프로도 보여줘"도 차트 요청이다(chart_type None = 실전 1이 고른 종류)
        wants_chart = reading.chart_only or reading.chart_type is not None or chart_request(question)[0]
        # 이월 턴 = 직전 표를 쓰되 질문을 다시 쓴 턴("부산은?" → 조건을 채운 독립 질문). 지시어 턴("그 표에서 …")은
        # 원문을 그대로 쓰므로 여기에 들지 않는다. 이월 턴만 "직전 표가 못 답하면 다시 쓴 문장으로 검색"을 한다.
        carried = (reading.use_previous_table and not reading.chart_only
                   and reading.resolved_question != question)
        # 멀티쿼리 분해는 새 질문 턴에서만 한다(이월·지시어·차트 턴은 직전 표 하나로 답함)
        split = None if reading.use_previous_table else split_question(question, parse, splitter)
        subs = split.sub_questions if split and len(split.sub_questions) > 1 else []
        return {"question": question, "resolved_question": reading.resolved_question, "interpreted_by": reading.by,
                "reused_table": reading.use_previous_table, "carried_over": carried, "fallback_searched": False,
                "chart_requested": wants_chart,
                "chart_type": reading.chart_type, "chart_only": reading.chart_only, "chart_note": None,
                "attempts": [], "stat": None, "table_key": None, "chart": None, "outcome": None, "degraded": False,
                "sub_questions": subs, "combine_op": split.op if subs else None,
                "split_by": split.by if subs else None, "sub_results": None, "combined": None}

    def after_context(state: AgentState) -> str:
        if state["chart_only"]:
            return "chart"
        if state.get("sub_questions"):
            return "multi"
        return "statistics" if state["reused_table"] else "search"

    def search_node(state: AgentState, config: RunnableConfig) -> dict:
        """표를 찾는다. 2차 리랭커가 실패했으면 degraded로 표시한다.

        - 새 질문: 사용자 원문으로 찾는다(해석기가 다시 쓴 문장이 아님. LLM의 영향을 이월 턴으로 좁힘).
        - 이월 턴인데 직전 표가 표 불일치로 거절함(after_statistics → 여기): 다시 쓴 독립 질문으로 찾는다.
          "부산은?"을 그대로 검색하면 실시간 검색은 거절한다(2026-11-09 실측). 다시 쓴 문장은 이전 질문의 지표·기간에
          지역만 바꾼 완전한 질문이라 맞는 표를 찾을 수 있다. 이 뒤로는 일반 턴처럼 후보 표에서 고른다.
        """
        fallback = bool(state.get("carried_over") and state["attempts"])
        question = state["resolved_question"] if fallback else state["question"]
        out: SearchOutput = call_tool(search_tables, {"question": question}, config).artifact
        degraded = any(s.get("node") == "rerank2" and s.get("stage2_error") for s in out.spans)
        update = {"search": out, "degraded": degraded}
        if fallback:  # 이제부터 직전 표가 아니라 새 검색 후보에서 고른다(이미 시도한 직전 표는 next_table이 건너뜀)
            update.update(reused_table=False, fallback_searched=True)
        return update

    def after_search(state: AgentState) -> str:
        # 시도하지 않은 후보가 있을 때만 실전 1로 간다. 이월 턴의 다시 검색이 직전 표만 찾으면, 직전 표의 거절로 끝낸다.
        out = state["search"]
        return "statistics" if out.route == "found" and next_table(state) is not None else "finalize"

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
        """멀티쿼리 턴: 하위 질문마다 검색하고, 표가 다르면 하위 질문마다 실전 1에 묻는다.

        - 같은 표 되돌림: 하위 질문이 모두 같은 표를 찾으면 나누지 않은 것으로 보고 원문 한 번으로 묻는다.
        - 하위 질문 하나가 검색 거절·실전 1 거절이면 나머지는 답하고, 그 하위 질문에는 거절 문장을 붙인다(지어내지 않음).
        - 대체 표 재시도·이월 다시 검색은 하지 않는다(하위 질문마다 첫 표 하나).
        """
        subs = state["sub_questions"]
        searches: list[SearchOutput] = [call_tool(search_tables, {"question": q}, config).artifact for q in subs]
        keys = [(candidate_keys(o) or [None])[0] if o.route == "found" else None for o in searches]
        degraded = any(s.get("node") == "rerank2" and s.get("stage2_error") for o in searches for s in o.spans)
        base = {"search": searches[-1], "degraded": degraded}
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
        """표 하나로 실전 1에 답을 받는다. 숫자는 실전 1의 코드가 계산한다.

        툴 인자 = 질문 + 표 키 + 조건 4개(기간·시도·분류). 질문은 해석된 문장(resolved_question)이다: 이월 턴이면
        "부산은?"이 아니라 "2025년 부산광역시에서 태어난 아이는 몇 명이에요?"를 넘긴다(실전 1은 문장에서 조건을 읽음).
        조건 인자는 그 문장을 실전 2와 같은 규칙으로 읽은 값이라, 이월이 맞는 조건을 담았는지가 인자에 드러난다.
        """
        key = next_table(state)
        question = state.get("resolved_question") or state["question"]
        stat = ask(question, key, config)
        answer = stat.answer
        attempts = [*state["attempts"], {"key": key, "outcome": answer.outcome,
                                         "reason": answer.refusal.reason if answer.refusal else None}]
        # 턴은 마지막으로 시도한 표에서 끝난다. 대체 표로 다시 들어오면 그 표의 결과로 덮어쓴다.
        # 평가 하네스도 "마지막 summarize_stat 호출"을 이 턴의 표·답으로 채점하므로 둘이 같은 기준이다.
        return {"attempts": attempts, "stat": stat, "table_key": key}

    def after_statistics(state: AgentState) -> str:
        last = state["attempts"][-1]
        if last["outcome"] == "answered":
            return "chart"
        mismatch = last["outcome"] == "refused" and last["reason"] in TABLE_MISMATCH
        if mismatch and state.get("carried_over") and not state.get("fallback_searched"):
            return "search"  # 이월 턴: 직전 표에 그 지역·기간이 없음 → 다시 쓴 문장으로 검색 (search_node)
        retry = (last["outcome"] == "refused" and last["reason"] in TABLE_MISMATCH
                 and len(state["attempts"]) <= alt_table_budget and next_table(state) is not None)
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

    def finalize_node(state: AgentState) -> dict:
        """outcome과 답 메시지를 정한다. 답한 표는 다음 턴의 "그 표"(active_table)가 되고, 해석된 질문과 차트 데이터는
        다음 턴의 이월·차트 요청에 쓰인다(active_question · active_chart)."""
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
                "messages": [AIMessage(stat.answer.text)]}

    graph = StateGraph(AgentState)
    graph.add_node("context", context_node)
    graph.add_node("search", search_node)
    graph.add_node("statistics", statistics_node)
    graph.add_node("multi", multi_node)
    graph.add_node("chart", chart_node)
    graph.add_node("finalize", finalize_node)
    graph.add_edge(START, "context")
    graph.add_conditional_edges("context", after_context, ["search", "statistics", "chart", "multi"])
    graph.add_conditional_edges("multi", after_multi, ["chart", "finalize"])
    graph.add_conditional_edges("search", after_search, ["statistics", "finalize"])
    graph.add_conditional_edges("statistics", after_statistics, ["statistics", "search", "chart", "finalize"])
    graph.add_edge("chart", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
