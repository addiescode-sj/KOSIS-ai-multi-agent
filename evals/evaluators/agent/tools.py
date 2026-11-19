"""Tool calls of a turn: which tools ran, in which order, with which arguments (alternate-table attempts set aside)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from evals.evaluators.query import field_correct
from evals.schemas import TurnExpect


def _norm(value: Any) -> Any:
    if value is None:
        return None
    return [str(v) for v in value] if isinstance(value, (list, tuple, set)) else str(value)


def _gave_up(call: dict) -> bool:
    """A summarize_stat call that did not answer from its table: refused, or the tool failed."""
    result = call.get("result")
    return call.get("status") == "error" or (isinstance(result, dict) and result.get("outcome") == "refused")


def final_calls(calls: list[dict]) -> list[dict]:
    """The calls a turn is graded on (T3-23): an alternate-table attempt is not a call of its own.

    A summarize_stat that refused (or failed) and is followed right away by a summarize_stat on another table_key is
    an attempt the agent moved on from; the later call stands for it (A refused -> B refused -> C answered leaves C).
    So a turn that answers from an alternate table is graded only on its final table and answer: lost_state when that
    table is not the expected one, wrong_numbers when the answer is wrong, never extra_call / false_refusal for the
    attempts. A repeated summarize_stat on the same table is not an attempt and still counts as a call."""
    out: list[dict] = []
    for call in calls:
        prev = out[-1] if out else None
        if (prev is not None and prev["name"] == call["name"] == "summarize_stat" and _gave_up(prev)
                and (prev.get("args") or {}).get("table_key") != (call.get("args") or {}).get("table_key")):
            out[-1] = call
        else:
            out.append(call)
    return out



def _subsequence(want: list[str], got: list[str]) -> bool:
    it = iter(got)
    return all(name in it for name in want)



def grade_tools(expect: TurnExpect, calls: list[dict]) -> tuple[dict, list[str]]:
    """Names (exact order and in-order subsequence), missing / extra calls per tool, and each graded argument field
    vs the same tool's k-th call with the L2 slot rule (table_key also accepts expect.alt_table_keys)."""
    want, got = [c.name for c in expect.tools], [c["name"] for c in calls]
    missing, extra = Counter(want) - Counter(got), Counter(got) - Counter(want)
    seen: Counter = Counter()
    args = []
    for call in expect.tools:
        same = [c for c in calls if c["name"] == call.name]
        pred = same[seen[call.name]] if seen[call.name] < len(same) else None
        seen[call.name] += 1
        for field, gold in call.args.items():
            value = _norm((pred.get("args") or {}).get(field)) if pred else None
            ok = pred is not None and (field_correct(field, gold, value)
                                       or (field == "table_key" and value in expect.alt_table_keys))
            args.append({"tool": call.name, "field": field, "ok": ok})
    errors = []
    if missing:
        errors.append("missing_call")
    if extra:
        errors.append("extra_call")
    if not missing and not extra and want != got:
        errors.append("wrong_order")
    if any(not a["ok"] for a in args):
        errors.append("wrong_args")
    return {"tools_ok": want == got, "order_ok": _subsequence(want, got), "missing": dict(missing), "extra": dict(extra),
            "args": args}, errors
