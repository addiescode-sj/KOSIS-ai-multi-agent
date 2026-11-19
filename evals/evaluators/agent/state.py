"""Multi-turn state of a turn: the table it ends on ("이거" / "그 표" / carried conditions) and whether it refused."""

from __future__ import annotations

from typing import Any

from evals.schemas import TurnExpect

STATE_TOOLS = ("summarize_stat", "make_chart")  # calls whose table_key is the table the turn works on


def first_key(result: Any) -> str | None:
    """Top table key of a search_tables result (phase 2 SearchOutput: picked, else candidates)."""
    if isinstance(result, dict):
        for field in ("picked", "candidates"):
            if (rows := result.get(field)) and isinstance(rows[0], dict):
                return rows[0].get("key")
    return None


def state_key(calls: list[dict]) -> str | None:
    """The table a turn ends on: the last summarize_stat / make_chart table_key, else the last search's top key.

    ponytail: read from the calls, not from the graph state, so it does not depend on T3-04's state field names.
    """
    for call in reversed(calls):
        if call["name"] in STATE_TOOLS and (call.get("args") or {}).get("table_key"):
            return call["args"]["table_key"]
    for call in reversed(calls):
        if call["name"] == "search_tables" and (key := first_key(call.get("result"))):
            return key
    return None


def refused(calls: list[dict]) -> bool:
    if not calls:
        return True
    for call in calls:
        result = call.get("result")
        if isinstance(result, dict) and (result.get("route") == "refuse" or result.get("outcome") == "refused"):
            return True
    return False


def _found(search: dict) -> bool:
    """A search that hands a table on (the graph's multi node: route found and a candidate key)."""
    result = search.get("result")
    return isinstance(result, dict) and result.get("route") == "found" and first_key(result) is not None


def sub_calls(calls: list[dict], n: int) -> list[dict | None] | None:
    """Per sub-question (search order), its summarize_stat call, or None when its search found no table.

    This is the order the graph's multi node leaves (src/kosis_agent/graph/main_graph.py): every sub-question's
    search_tables first, then one summarize_stat per search that found a table, in the same order. None when the
    calls are not that shape: not n searches (the turn was not split) or a summarize_stat count other than the found
    searches (all parts found one table and the graph asked the original question once).
    """
    searches = [c for c in calls if c["name"] == "search_tables"]
    stats = iter(c for c in calls if c["name"] == "summarize_stat")
    found = [_found(s) for s in searches]
    if len(searches) != n or sum(found) != sum(c["name"] == "summarize_stat" for c in calls):
        return None
    return [next(stats) if f else None for f in found]


def refused_parts(calls: list[dict], n: int) -> list[bool]:
    """Each sub-question's refusal: its search found no table, or its summarize_stat refused. A turn that is not
    split into n parts answers every part with its one answer, so each part takes the whole turn's refusal."""
    subs = sub_calls(calls, n)
    if subs is None:
        return [refused(calls)] * n
    return [s is None or (isinstance(s.get("result"), dict) and s["result"].get("outcome") == "refused") for s in subs]


def grade_state(expect: TurnExpect, calls: list[dict]) -> tuple[dict, list[str]]:
    """state_key vs expect.table_key (or an alternate gold table) -> lost_state; refusal vs expect.refuse, or per
    sub-question vs expect.refuse_parts (T3-38: one missed_refusal / false_refusal per wrong sub-question)."""
    key = state_key(calls)
    did_refuse = refused(calls)
    errors = []
    state_ok = None if expect.table_key is None else (key == expect.table_key or key in expect.alt_table_keys)
    if state_ok is False:
        errors.append("lost_state")
    out: dict = {"state_key": key, "state_ok": state_ok, "refused": did_refuse}
    if expect.refuse_parts is not None:
        got = refused_parts(calls, len(expect.refuse_parts))
        wrong = [{"part": k, "error": "missed_refusal" if want else "false_refusal"}
                 for k, (want, did) in enumerate(zip(expect.refuse_parts, got)) if want != did]
        errors += [w["error"] for w in wrong]
        return {**out, "refused_parts": got, "refusal_errors": wrong}, errors
    if expect.refuse and not did_refuse:
        errors.append("missed_refusal")
    if not expect.refuse and did_refuse:
        errors.append("false_refusal")
    return out, errors
