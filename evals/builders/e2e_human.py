"""T3-56: the team's human e2e golden (questions.csv + facts.csv) -> e2e_golden.jsonl + e2e_golden_calc.jsonl.

    PYTHONPATH=.:src:vendor/phase2/src:vendor/phase1:vendor/phase1/src \
        uv run python -m evals.builders.e2e_human <questions.csv> <facts.csv> [out_dir]

facts.csv names cells by labels (item, "name=label;..." dims, period); grading matches cells by source_ref, so every
cell fact is matched to its Parquet cell by value (a display-rounded value within half its last digit), period, item
name and dim labels. A question over several tables is one CalcItem with table_keys (phase 1 T1-30), its facts from
all of them. Refuse and clarify turns get no calc item, as in the T3-46 golden. Facts that match no cell (or no
Parquet file) are left out with everything derived from them, and listed; the exit code is 1 then.
"""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq
from kosis_ai.schemas import Cell, Fact

from evals.evaluators.alternates import VALUES, cells_with_values, table_sha
from evals.schemas import DATASETS, AgentScenario, CalcItem

IDENT = re.compile(r"[A-Za-z_]\w*")
TOTALS = {"계", "총계", "합계", "전체", "소계", "전국"}
OUTCOME = {"answerable": "answered", "default_allowed": "answered", "not_answerable": "refused", "clarify": "clarify"}


def norm(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "")


def parse_dims(text: str) -> dict[str, str]:
    """'시도별=서울특별시;연령별=30세' -> {name: label}."""
    return {k.strip(): v.strip() for k, _, v in (p.partition("=") for p in (text or "").split(";") if p)}


def find_cell(row: dict, problems: list[str]) -> Cell | None:
    """The one Parquet cell a facts.csv cell row names, else None (the reason goes to problems)."""
    path = VALUES / f"{row['table_key']}.parquet"
    if not path.exists():
        problems.append(f"{row['qid']} {row['fact_id']}: no parquet {row['table_key']}")
        return None
    if row["value"] != "":
        cands = cells_with_values(path, frozenset([float(row["value"])]))
        if not cands:  # a display-rounded value (1.24 for 1.2421...): within half its last digit
            digits = len(row["value"].split(".")[1]) if "." in row["value"] else 0
            tol, v = 0.5 * 10**-digits, float(row["value"])
            rows = pq.read_table(path, filters=[("period", "=", row["period"])]).to_pylist()
            cands = [Cell.from_row(r) for r in rows if r["value"] is not None and abs(r["value"] - v) <= tol]
    else:  # a symbol cell ('-', 'X') has no value to filter on
        raw = row["raw"].strip()
        cands = [Cell.from_row(r) for r in pq.read_table(path).to_pylist() if r["value_raw"].strip() == raw]
    want = {norm(k): norm(v) for k, v in parse_dims(row["dims"]).items()}
    cands = [c for c in cands if c.period == row["period"]]
    cands = [c for c in cands if norm(c.item_name) == norm(row["item"])] or cands

    def dims_ok(c: Cell) -> bool:
        have = {norm(d.name): norm(d.label) for d in c.dims}
        return all(have.get(k) == v or v in have.values() for k, v in want.items())  # a name may be spelled apart

    cands = [c for c in cands if dims_ok(c)]
    if len(cands) > 1:  # dims the csv left out: prefer totals
        cands = [c for c in cands if all(norm(d.label) in TOTALS | set(want.values()) for d in c.dims)] or cands
    if len(cands) != 1:
        problems.append(f"{row['qid']} {row['fact_id']}: {len(cands)} cells ({row['table_key']} {row['item']} "
                        f"{row['dims']} {row['period']} = {row['value'] or row['raw']})")
        return None
    return cands[0]


def to_fact(row: dict, cell: Cell | None) -> Fact:
    note = row["note"] or None
    if cell is not None:
        return cell.to_fact(row["fact_id"]).model_copy(update={"note": note})
    return Fact(id=row["fact_id"], value=float(row["value"]), unit=row["unit"] or None, derivation=row["derivation"],
                round=int(row["round"]) if row["round"] else None, note=note)


def build(questions: list[dict], facts: list[dict]) -> tuple[list[AgentScenario], list[CalcItem], list[str]]:
    by_q: dict[str, list[dict]] = defaultdict(list)
    for r in facts:
        by_q[r["qid"]].append(r)
    problems: list[str] = []
    scenarios, calc = [], []
    for q in questions:
        qid, kind, qtype = q["qid"], q["kind"], q["type"] or None
        keys = [k for k in q["table_keys"].split(";") if k]
        labels: dict = {"reading": "new", "outcome": OUTCOME[kind]}
        if qtype:
            labels["qtype"] = qtype
        if kind == "not_answerable":
            labels |= {"route": "refuse", "refusal_reasons": [r for r in q["allowed_reasons"].split(";") if r]}
        elif kind == "clarify":
            labels["route"] = "clarify"
        elif len(keys) == 1:
            labels["route"] = "rag"  # a multi-table question's route depends on the architecture: not graded
        if kind == "default_allowed":
            labels["default_applied"] = True
        # today's agent: one search and one summarize_stat per table (a multi-table question splits per table)
        tools = ([{"name": "search_tables"}] * max(len(keys), 1)
                 + [{"name": "summarize_stat", "args": {"table_key": k}} for k in keys])
        expect: dict = {"labels": labels, "tools": tools, "table_key": keys[-1] if keys else None,
                        "refuse": kind == "not_answerable"}
        notes = [q["notes"]] if q["notes"] else []
        if kind in ("answerable", "default_allowed"):
            kept: list[tuple[str, Fact]] = []
            missing: set[str] = set()
            for row in by_q[qid]:  # inputs come before what is derived from them
                cell = find_cell(row, problems) if row["table_key"] else None
                if (row["table_key"] and cell is None) or set(IDENT.findall(row["derivation"])) & missing:
                    missing.add(row["fact_id"])
                    continue
                kept.append((row["role"], to_fact(row, cell)))
            if missing:
                notes.append(f"not graded (no cell): {', '.join(sorted(missing))}")
            if not any(role == "required" for role, _ in kept):
                notes.append("numbers not graded: no required fact left")
                scenarios.append(AgentScenario.model_validate(
                    {"sid": qid, "split": q["split"] or "test", "source": q["type"] or kind,
                     "turns": [{"question": q["question"], "expect": expect}], "notes": " | ".join(notes)}))
                continue
            graded = [k for k in keys if (VALUES / f"{k}.parquet").exists()]  # a table without Parquet: no cells
            if graded != keys:
                notes.append(f"table not in data/values: {', '.join(k for k in keys if k not in graded)}")
            calc.append(CalcItem(
                qid=qid, kind=kind, type=qtype, question=q["question"], table_key=graded[0], table_sha256=table_sha(graded[0]),
                table_keys=graded if len(graded) > 1 else [], split=q["split"] or None,
                default_applied=dict(kv.split("=", 1) for kv in q["default_applied"].split(";") if kv),
                required_facts=[f for role, f in kept if role == "required"],
                aux_facts=[f for role, f in kept if role == "aux"]))
            expect["calc_qid"] = qid
        scenarios.append(AgentScenario.model_validate(
            {"sid": qid, "split": q["split"] or "test", "source": q["type"] or q["refusal_reason"] or kind,
             "turns": [{"question": q["question"], "expect": expect}], "notes": " | ".join(notes)}))
    return scenarios, calc, problems


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) not in (2, 3):
        print(__doc__)
        return 2
    out = Path(args[2]) if len(args) == 3 else DATASETS
    scenarios, calc, problems = build(read_csv(Path(args[0])), read_csv(Path(args[1])))
    out.mkdir(parents=True, exist_ok=True)
    (out / "e2e_golden.jsonl").write_text("".join(s.model_dump_json(exclude_none=True) + "\n" for s in scenarios),
                                          encoding="utf-8")
    (out / "e2e_golden_calc.jsonl").write_text(
        "".join(c.model_dump_json(exclude_none=True, exclude_defaults=True) + "\n" for c in calc), encoding="utf-8")
    multi = sum(1 for c in calc if c.table_keys)
    print(f"scenarios {len(scenarios)} · calc {len(calc)} (multi-table {multi}) -> {out}")
    print("\n".join(problems) or "every cell fact matched")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
