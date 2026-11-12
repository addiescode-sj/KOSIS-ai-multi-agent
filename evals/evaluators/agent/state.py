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



def grade_state(expect: TurnExpect, calls: list[dict]) -> tuple[dict, list[str]]:
    """state_key vs expect.table_key (or an alternate gold table) -> lost_state; refusal vs expect.refuse."""
    key = state_key(calls)
    did_refuse = refused(calls)
    errors = []
    state_ok = None if expect.table_key is None else (key == expect.table_key or key in expect.alt_table_keys)
    if state_ok is False:
        errors.append("lost_state")
    if expect.refuse and not did_refuse:
        errors.append("missed_refusal")
    if not expect.refuse and did_refuse:
        errors.append("false_refusal")
    return {"state_key": key, "state_ok": state_ok, "refused": did_refuse}, errors
