"""Charts of a turn (T3-37, KOS-197): the drawn data must be phase 1 facts, and the automatic chart choice must match.

- data: every number a make_chart call draws (the values of the drawn ChartSpec's points) must be one of the fact
  values of the same turn's summarize_stat answers, or, for a chart-only follow-up turn (no summarize_stat), of the
  conversation's latest summarize_stat answers. Otherwise chart_data_mismatch.
  Exception: the context phase 1 draws beside the facts by its contract (ChartPoint without fact_id) — cells of the
  chart's own table (other periods / candidates) and a pie's rest slice — passes and is counted in
  chart_context_values (plans/T3-37.md: 13 of 29 mock-run charts drew such context). A stacked bar's rest to 100% is
  not a point of the ChartSpec (chart_data adds it for the screen), so it is not graded.
- automatic choice: when the user named no chart type, expect.auto_chart (bar | line | pie, from the calculation
  item type) must be the family of the kind make_chart drew. A drawn chart of another family is wrong_chart_type.

The recorded make_chart result (the tool's content, src/kosis_agent/services/chart.py) carries only
{table_key, chart_type, kind, drawn, note}. Its argument `chart` is the phase 1 ChartSpec; redrawing it (phase 1
kosis_chat.chart.module.redraw) changes the kind and may cut points but never adds a number, so the argument's points
are the numbers graded. No Vega-Lite (KOS-202).
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Callable, Iterable

from kosis_ai.schemas import ChartSpec

from evals.evaluators.agent.numbers import stat_answers
from evals.schemas import ROOT, TurnExpect

# phase 1 ChartSpec.kind -> the chart family a user names (kosis_ai.schemas.ChartKind)
FAMILY = {"bar": "bar", "timeline": "bar", "grouped_bar": "bar", "stacked_bar": "bar", "line": "line",
          "multi_line": "line", "pie": "pie"}


def turn_values(calls: list[dict]) -> list[float] | None:
    """Fact values of the turn's summarize_stat answers; None when the turn called no summarize_stat."""
    answers = stat_answers(calls)
    if not answers:
        return None
    return [f.value for a in answers if a is not None for f in a.result.facts if f.value is not None]


def drawn_spec(call: dict) -> ChartSpec | None:
    """The ChartSpec a make_chart call drew from (its `chart` argument); None when nothing was drawn or the argument
    does not validate."""
    result = call.get("result") if isinstance(call.get("result"), dict) else {}
    args = call.get("args") or {}
    if call.get("status", "ok") != "ok" or not args.get("chart") or result.get("drawn") is False:
        return None
    try:
        return ChartSpec.model_validate(args["chart"])
    except ValueError:
        return None


def drawn_values(spec: ChartSpec) -> list[float]:
    """The numbers a chart draws: its points' values."""
    return [p.value for p in spec.points() if isinstance(p.value, (int, float)) and not isinstance(p.value, bool)]


def drawn_family(call: dict) -> str | None:
    result = call.get("result")
    if not isinstance(result, dict) or result.get("drawn") is False:
        return None
    return FAMILY.get(result.get("kind"))


def _among(value: float, pool) -> bool:
    return any(math.isclose(value, p, rel_tol=1e-9, abs_tol=1e-9) for p in pool)


@lru_cache(maxsize=32)
def table_numbers(key: str) -> frozenset[float]:
    """Every number of a table: its Parquet `value` column only (cheap even for large tables, T3-39), {} when the
    file cannot be read."""
    import pyarrow.parquet as pq

    try:
        column = pq.read_table(ROOT / "data" / "values" / f"{key}.parquet", columns=["value"]).column("value")
    except (OSError, KeyError, ValueError):
        return frozenset()
    return frozenset(v for v in column.drop_null().to_pylist() if isinstance(v, (int, float)))


def table_values(key: str | None, values_of: Callable[[str], Iterable[float]] | None) -> Iterable[float]:
    if not key or values_of is None:
        return ()
    try:
        return values_of(key)
    except (OSError, ValueError):
        return ()


def _in_table(value: float, numbers: Iterable[float]) -> bool:
    if isinstance(numbers, (set, frozenset)) and value in numbers:  # exact hit without a scan
        return True
    return _among(value, numbers)


def unmatched_values(spec: ChartSpec, key: str | None, facts: list[float],
                     values_of: Callable[[str], Iterable[float]] | None) -> tuple[list[float], int]:
    """(drawn numbers that are not data, numbers drawn as context). A drawn number is data when it is a fact value;
    phase 1 also draws context that is not a fact (ChartPoint without fact_id: the other periods of a series, the
    other candidates of a ranking = cells of the same table, and a pie's rest slice = the total fact minus the
    drawn parts), so those pass too and are counted apart. Anything else (a number from nowhere, another table's
    cell) is a mismatch."""
    values = drawn_values(spec)
    others = [v for v in values if not _among(v, facts)]
    if not others:
        return [], 0
    rest_ok = spec.kind == "pie" and _among(sum(values), facts)  # the slices add up to a total fact
    numbers = table_values(key, values_of) if not rest_ok else ()
    unmatched = [v for v in others if not rest_ok and not _in_table(v, numbers)]
    return unmatched, len(others) - len(unmatched)


def grade_chart(expect: TurnExpect, calls: list[dict], facts: list[float],
                values_of: Callable[[str], Iterable[float]] | None = None) -> tuple[dict, list[str]]:
    """facts = the fact values a chart of this turn may draw (turn_values of this turn, else the latest turn's);
    values_of(table_key) = every number of the table, for the context rows phase 1 draws beside the facts."""
    charts = [c for c in calls if c["name"] == "make_chart"]
    checked, unmatched, context = False, [], 0
    for call in charts:
        spec = drawn_spec(call)
        if spec is None or not drawn_values(spec):
            continue
        checked = True
        bad, ctx = unmatched_values(spec, (call.get("args") or {}).get("table_key"), facts, values_of)
        unmatched += bad
        context += ctx
    kind = drawn_family(charts[-1]) if charts else None
    auto_ok = None if expect.auto_chart is None else kind == expect.auto_chart
    errors = ["chart_data_mismatch"] * bool(unmatched) + ["wrong_chart_type"] * (auto_ok is False and bool(charts))
    return {"chart_turn": any(c.name == "make_chart" for c in expect.tools), "chart_data_ok": not unmatched if checked else None,
            "chart_unmatched": unmatched[:5], "chart_context_values": context, "chart_kind": kind,
            "auto_chart": expect.auto_chart, "auto_chart_ok": auto_ok}, errors
