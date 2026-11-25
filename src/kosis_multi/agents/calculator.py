"""④ 계산 에이전트: 표 하나로 실전 1(summarize_stat)에 답을 받는다. 숫자는 여기서만 나온다(실전 1 코드 계산).

- 한 질문: ③이 고른 표(plan_key)로 한 번. 결과는 attempts에 쌓고, 턴은 마지막으로 시도한 표에서 끝난다.
- 멀티쿼리: 하위 질문마다 한 번(표가 없는 하위 질문은 거절 문장). 계산 요청이고 둘 다 답했으면 combine_values.
하위 질문은 순서대로 부른다(결정 5: 하위 질문 병렬은 뺌, 멀티쿼리 턴이 약 3%).
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.runnables import RunnableConfig

from kosis_agent.graph.main_graph import CONDITION_FIELDS, NO_TABLE_TEXT
from kosis_chat.schemas.io import StatOutput
from kosis_multi.tools import Toolbox, call_tool
from kosis_rag.parse.schemas import ParsedQuery


def with_note(text: str, rewritten: str | None) -> str:
    """다듬은 질문으로 답했으면 답 끝에 어떻게 읽었는지 한 줄 알린다(S4b)."""
    return f"{text}\n\n(질문을 \"{rewritten}\"(으)로 읽고 답했어요.)" if rewritten else text


def make_calculator(tools: Toolbox, parse: Callable[[str], ParsedQuery]):
    def ask(question: str, key: str, config: RunnableConfig, rewritten: str | None = None) -> StatOutput:
        """summarize_stat 한 번: 질문 + 표 키 + 조건 4개(채점용 인자). 다듬은 질문(S4b)이 있으면 실전 1은 그것으로 답하고,
        조건도 그것으로 읽는다("작년" → 2025). question 인자는 사용자 원 질문 그대로 남긴다."""
        parsed = parse(rewritten or question)
        args = {"question": question, "table_key": key, **{f: getattr(parsed, f) for f in CONDITION_FIELDS}}
        if rewritten:
            args["rewritten"] = rewritten
        return call_tool(tools.summarize_stat, args, config).artifact

    def attempt(key: str, stat: StatOutput) -> dict:
        answer = stat.answer
        return {"key": key, "outcome": answer.outcome, "reason": answer.refusal.reason if answer.refusal else None}

    def single(state: dict, config: RunnableConfig) -> dict:
        key = state["plan_key"]
        stat = ask(state.get("resolved_question") or state["question"], key, config, state.get("calc_question"))
        return {"attempts": [*state["attempts"], attempt(key, stat)], "stat": stat, "table_key": key}

    def multi(state: dict, config: RunnableConfig) -> dict:
        results, attempts, last = [], [], None
        rewrites = state.get("sub_calc_questions") or [None] * len(state["sub_questions"])
        for question, out, key, rewritten in zip(state["sub_questions"], state["sub_searches"], state["sub_keys"],
                                                  rewrites):
            if key is None:
                text = (out.refusal_text or NO_TABLE_TEXT) if out.route == "refuse" else NO_TABLE_TEXT
                results.append({"question": question, "table_key": None, "outcome": "refused",
                                "reason": out.refusal_reason, "text": text, "answer": None})
                continue
            stat = ask(question, key, config, rewritten)
            a = attempt(key, stat)
            attempts.append(a)
            results.append({"question": question, "table_key": key, "outcome": a["outcome"], "reason": a["reason"],
                            "text": with_note(stat.answer.text, rewritten),
                            "answer": stat.answer.model_dump(mode="json")})
            if a["outcome"] == "answered":
                last = (stat, key, question)
        combined = None
        if state.get("combine_op") and len(results) == 2 and all(r["outcome"] == "answered" for r in results):
            args = {"op": state["combine_op"], "answers": [r["answer"] for r in results]}
            combined = call_tool(tools.combine_values, args, config).artifact
        update = {"sub_results": results, "combined": combined, "attempts": attempts}
        if last is not None:  # 다음 턴의 "그 표" = 마지막으로 답한 하위 질문의 표
            update.update(stat=last[0], table_key=last[1], resolved_question=last[2])
        return update

    def calculate(state: dict, config: RunnableConfig) -> dict:
        return multi(state, config) if state.get("sub_keys") is not None else single(state, config)

    return calculate
