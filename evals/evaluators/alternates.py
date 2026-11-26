"""Alternate gold tables by cell values (plans/T3-41.md): a table that holds every golden cell fact answers the question too.

A table is an alternate for a calculation item when, for every cell fact (required + aux) of the item, it has a cell
with the same period and value (calculation.close), the same 시도 (official names; a golden fact with no region needs a
national / region-free cell), the golden non-region labels or a total for each, no other breakdown than totals, and a
compatible unit; facts that share one item in the golden table must share one item in the alternate too. No LLM: the
numbers must be the golden numbers, so accepting an alternate never accepts a different number.

Two uses, one rule:
- grading (remap): the e2e / agent answer came from table X != the golden table -> the golden item with its source_refs
  moved to X's cells (cells the answer cited first), graded by the unchanged calculation grader against X's Parquet;
- gold sets (scan -> evals/datasets/calculation_alternates.jsonl): which tables count as right in locate() and the
  ragas id metrics, independent of any retriever. `auto` labels only (all values integers >= 100); smaller values and
  rates are `needs_review` (more likely to match by chance) and stay out until a person accepts them.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from kosis_ai.schemas import Cell, Fact

from evals.evaluators import calculation
from evals.schemas import CalcItem, ROOT

VALUES = ROOT / "data" / "values"
ALTERNATES = ROOT / "evals" / "datasets" / "calculation_alternates.jsonl"
TOTALS = {"계", "총계", "합계", "전체", "소계", "전국"}
TARGET_KINDS = ("answerable", "default_allowed")


def official() -> dict[str, str]:
    from evals.runners.e2e import OFFICIAL

    return OFFICIAL


def cell_facts(item: CalcItem) -> list[Fact]:
    return [f for f in [*item.required_facts, *item.aux_facts] if f.source_ref]


def matchable(item: CalcItem) -> bool:
    """Only items whose cell facts all have a number (a symbol cell such as '-' cannot be found by value)."""
    facts = cell_facts(item)
    return item.kind in TARGET_KINDS and bool(facts) and all(f.value is not None for f in facts)


def fits(fact: Fact, cell: Cell) -> bool:
    """The match rule for one golden cell fact and one candidate cell."""
    if cell.period != fact.period or cell.value is None or not calculation.close(fact.value, cell.value, None):
        return False
    names = official()
    labels = [d.label for d in cell.dims if d.label]
    golden = [v for k, v in (fact.dims or {}).items() if k != "항목"]  # the item is item_name, not a breakdown
    regions = {names[v] for v in golden if v in names}
    if {names[v] for v in labels if v in names} != regions:  # same 시도; none (national) when the golden fact has none
        return False
    others = [v for v in golden if v not in names]
    if any(v not in labels and v not in TOTALS for v in others):
        return False
    if any(v not in names and v not in TOTALS and v not in others for v in labels):  # extra breakdowns must be totals
        return False
    unit = cell.unit_or_suffix()
    return not (fact.unit and unit and fact.unit != unit)


def match(item: CalcItem, cells: list[Cell], cited: frozenset[str] = frozenset()) -> dict[str, str] | None:
    """{fact id: cell ref} when `cells` (of one table) hold every cell fact of the item, else None. Facts that share a
    golden item must land on one item of the table; among equal candidates the cells the answer cited win."""
    facts = cell_facts(item)
    found = {f.id: [c for c in cells if fits(f, c)] for f in facts}
    if not facts or any(not v for v in found.values()):
        return None
    groups: dict[str, list[Fact]] = defaultdict(list)
    for f in facts:
        groups[f.source_ref.split("|")[1]].append(f)
    out = {}
    for group in groups.values():
        common = set.intersection(*({c.item_id for c in found[f.id]} for f in group))
        if not common:
            return None
        for f in group:
            pool = sorted((c for c in found[f.id] if c.item_id in common), key=lambda c: (c.ref() not in cited, c.ref()))
            out[f.id] = pool[0].ref()
    return out


def remapped(item: CalcItem, table_key: str, refs: dict[str, str]) -> CalcItem:
    """The golden item with its cell facts moved to the alternate table's cells (values, derivations, rounding kept)."""
    move = lambda facts: [f.model_copy(update={"source_ref": refs[f.id]}) if f.id in refs else f for f in facts]  # noqa: E731
    return item.model_copy(update={"table_key": table_key, "table_sha256": table_sha(table_key),
                                   "required_facts": move(item.required_facts), "aux_facts": move(item.aux_facts)})


@lru_cache(maxsize=256)
def table_sha(table_key: str) -> str:
    path = VALUES / f"{table_key}.parquet"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


@lru_cache(maxsize=1024)  # the agent layer asks the same (table, golden values) on several turns and checks (T3-42)
def cells_with_values(path: Path, values: frozenset[float]) -> list[Cell]:
    """The cells of one Parquet table whose value is in `values` (one filtered read, any table size)."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    if not path.exists() or not values:
        return []
    wanted = pa.array(sorted(values), type=pa.float64())
    out = []
    for batch in pq.ParquetFile(path).iter_batches(batch_size=200_000):  # big tables: never all rows at once (T3-39)
        hit = batch.filter(pc.is_in(batch.column("value"), value_set=wanted))
        out += [Cell.from_row(r) for r in hit.to_pylist()]
    return out


def alternate_item(item: CalcItem, table_key: str | None, cited: frozenset[str] = frozenset(),
                   values_dir: Path | None = None) -> CalcItem | None:
    """For grading: the item remapped to `table_key` when that table is an alternate (by the rule), else None.
    None for the golden table itself, so L3 (oracle, golden table) and right-table answers grade as before."""
    if not table_key or table_key in item.tables() or len(item.tables()) > 1 or not matchable(item):  # T3-56: one table only
        return None
    path = (values_dir or VALUES) / f"{table_key}.parquet"
    cells = cells_with_values(path, frozenset(float(f.value) for f in cell_facts(item)))
    refs = match(item, cells, cited)
    return remapped(item, table_key, refs) if refs else None


def status(item: CalcItem) -> str:
    """auto when every golden cell value is an integer >= 100 (a chance match is unlikely), else needs_review."""
    big = all(f.value is not None and abs(f.value) >= 100 and float(f.value).is_integer() for f in cell_facts(item))
    return "auto" if big else "needs_review"


def scan(items: list[CalcItem], values_dir: Path | None = None) -> list[dict]:
    """Every table of values_dir checked against every matchable item: one label per (item, alternate table).
    The golden table is kept out (it is always right); `self_found` records whether the rule finds it (self-check)."""
    targets = [i for i in items if matchable(i)]
    values = frozenset(float(f.value) for i in targets for f in cell_facts(i))
    labels, self_found = [], set()
    for path in sorted((values_dir or VALUES).glob("*.parquet")):
        cells = cells_with_values(path, values)
        if not cells:
            continue
        # T3-46: index by value so each item scans only its own values' cells (cells are exact is_in hits, so
        # fits() never accepts a cell outside them); 0/1/small rates of 525 items made the full scan quadratic
        by_value: dict[float, list[Cell]] = defaultdict(list)
        for c in cells:
            by_value[c.value].append(c)
        for item in targets:
            wanted = {float(f.value) for f in cell_facts(item)}
            if any(v not in by_value for v in wanted):
                continue
            if (refs := match(item, [c for v in wanted for c in by_value[v]])) is None:
                continue
            if path.stem == item.table_key:
                self_found.add(item.qid)
                continue
            labels.append({"qid": item.qid, "table_key": path.stem, "fact_refs": refs, "status": status(item)})
    return [*labels, *({"qid": i.qid, "table_key": i.table_key, "self_check": i.qid in self_found} for i in targets)]


def load(path: Path = ALTERNATES) -> dict[str, set[str]]:
    """qid -> alternate table keys that count as right (auto and accepted labels). Empty without the file."""
    out: dict[str, set[str]] = defaultdict(set)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and (d := json.loads(line)).get("status") in ("auto", "accepted"):
                out[d["qid"]].add(d["table_key"])
    return dict(out)


def sha(path: Path = ALTERNATES) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"
