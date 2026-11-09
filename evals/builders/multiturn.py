"""Multi-turn dev scenarios from the calculation golden set (T3-27, KOS-177). Data only, no LLM.

    python -m evals.builders.multiturn            # writes the three files below into evals/datasets/

Every golden table has two or more calculation_golden items, so items of one table chain into conversations whose
turns have a graded answer. Six kinds (plans/T3-27.md):

    R  reference     turn 2 = "그 표에서 " + another golden question of the same table   -> no search, same table
    E  ellipsis      turn 2 = "{region}은?" after a one-fact value question             -> same table, new region
    X  correction    turn 2 = "아니 {year}년으로" after a one-fact value question        -> same table, new period
    S  topic switch  turn 2 = a golden question of another table, verbatim               -> new search, no carry-over
    C  chart         turn 2 = "그 표로 막대그래프 그려줘" after a trend / compare question -> make_chart only
    M  multi-request turn 1 = a golden question + " 꺾은선 그래프로도 보여줘"            -> search, stat, make_chart

E and X answers are built by code, not by people: the one-fact item's source cell with its region code or year
swapped, read from the same Parquet file. A cell that is missing or holds a symbol makes no scenario (nothing is
made up). Those answers go to calculation_golden_multiturn.jsonl (pass it with --calc-golden next to the real one).
The _structure file is the same scenarios without calc_qid, for runs with the mock phase 1 model (it cannot write
the numbers, so every graded turn would fail wrong_numbers and hide the structure metrics).
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

from kosis_ai.schemas import Cell, Fact
from kosis_rag.shared.vocab import SIDO

from evals.schemas import DATASETS, AgentScenario, CalcItem

ROOT = Path(__file__).resolve().parents[2]
CALC = DATASETS / "calculation_golden.jsonl"
OUT = DATASETS / "agent_scenarios_multiturn_dev.jsonl"
OUT_STRUCTURE = DATASETS / "agent_scenarios_multiturn_dev_structure.jsonl"
OUT_CALC = DATASETS / "calculation_golden_multiturn.jsonl"

# one-fact value items that cannot carry a region / year swap (plans/T3-27.md, review 2)
EXCLUDE = {"S-VAL-06": "birth-cohort question, no region; its year is a cohort bound",
           "S-VAL-08": "national-sum trap (the answer is 전국, not a sum of regions)"}
REGION_DIM_WORDS = ("행정구역", "시도")
OFFICIAL = {name: name for name in SIDO} | {alias: name for name, aliases in SIDO.items() for alias in aliases}
SHORT = {name: min(aliases, key=len) for name, aliases in SIDO.items()}  # 부산광역시 -> 부산

REFERENCE = ["그 표에서 ", "같은 표에서 ", "이 표로 "]
ELLIPSIS = ["{r}{eun}?", "{r}{eun}요?", "그럼 {r}{eun} 어때?"]
CORRECTION = ["아니 {y}년으로", "{y}년 기준으로 다시", "{y}년{eun}?"]
CHART = [("그 표로 막대그래프 그려줘", "bar"), ("꺾은선 그래프로 보여줘", "line"), ("이걸 막대 차트로 그려줘", "bar")]
MULTI = [(" 꺾은선 그래프로도 보여줘", "line"), (" 막대그래프로도 그려줘", "bar")]
# Turn-2 wordings the builder above does not use, for the generalisation check (plans/T3-27.md, scope added
# 2026-11-09). Written by the agent after the rules, before any rule change, so they are not fully independent of the
# rules (author bias, noted in the plan); a user-written set still has to replace them.
PARAPHRASE = {
    "E": ["{r} 쪽 숫자도 알려줘", "{r} 기준으로는?", "{r}{eun} 얼마나 돼?", "{r}도 궁금해", "{r}{eun} 어떻게 나와?"],
    "X": ["{y}년 걸로 바꿔줘", "{y}년 기준이면?", "그럼 {y}년{eun}?", "{y}년 수치로 다시 알려줘", "{y}년 것도 보여줘"],
    "C": [("막대로 보여줘", "bar"), ("선으로 그려줄 수 있어?", "line"), ("이거 막대 그래프로 바꿔줘", "bar"),
          ("꺾은선으로 시각화해줘", "line")],
}
OUT_PARAPHRASE = DATASETS / "agent_scenarios_multiturn_paraphrase_structure.jsonl"
CAP = 15  # C and M scenarios each, so chart kinds do not outweigh the carry-over kinds in the overall rates


def eun(word: str) -> str:
    """은 after a final consonant, else 는 (Korean topic particle)."""
    last = word[-1]
    return "은" if "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 else "는"


def stat_call(key: str, **args) -> dict:
    return {"name": "summarize_stat", "args": {"table_key": key, **args}}


def first_turn(item: CalcItem, question: str | None = None, extra: list[dict] | None = None) -> dict:
    """Turn 1: a golden question answered after a search."""
    return {"question": question or item.question,
            "expect": {"tools": [{"name": "search_tables"}, stat_call(item.table_key), *(extra or [])],
                       "table_key": item.table_key, "calc_qid": item.qid}}


class Cells:
    """Cells of one table by source_ref, and the 시도 codes of its region dimension."""

    def __init__(self, key: str):
        rows = pq.read_table(ROOT / "data" / "values" / f"{key}.parquet").to_pylist()
        self.by_ref = {c.ref(): c for c in map(Cell.from_row, rows)}

    def swap(self, ref: str, *, region: str | None = None, year: str | None = None) -> Cell | None:
        """The cell of `ref` with its region dimension set to `region` (official 시도 name) or its year replaced."""
        src = self.by_ref.get(ref)
        if src is None:
            return None
        for cell in self.by_ref.values():
            if cell.item_id != src.item_id or len(cell.dims) != len(src.dims):
                continue
            want_period = src.period if year is None else year + src.period[4:]
            if cell.period != want_period:
                continue
            same = True
            for a, b in zip(src.dims, cell.dims):
                if region is not None and any(w in a.name for w in REGION_DIM_WORDS):
                    same &= OFFICIAL.get(b.label.strip()) == region
                else:
                    same &= a.code == b.code
            if same:
                return cell if cell.value_kind == "ok" else None
        return None


def region_of(fact: Fact) -> tuple[str, str] | None:
    """(dim name, official 시도 name or 전국) of a fact's region dimension, None when it has no 시도-level region."""
    for name, label in (fact.dims or {}).items():
        if any(w in name for w in REGION_DIM_WORDS):
            label = label.strip()
            if label == "전국" or label in OFFICIAL:
                return name, OFFICIAL.get(label, label)
    return None


def synthetic(item: CalcItem, cell: Cell, qid: str, question: str) -> CalcItem:
    fact = item.required_facts[0]
    dims = {**(fact.dims or {}), **{d.name: d.label for d in cell.dims}}
    new = fact.model_copy(update={"value": cell.value, "period": cell.period, "dims": dims, "source_ref": cell.ref()})
    return CalcItem(qid=qid, kind="answerable", type="value", question=question, table_key=item.table_key,
                    table_sha256=item.table_sha256, required_facts=[new], eda_traps=["synthetic_multiturn"],
                    split="dev")


def build(calc_path: Path = CALC, seed: int = 0) -> tuple[list[AgentScenario], list[CalcItem]]:
    rng = random.Random(seed)
    items = [CalcItem.model_validate_json(line) for line in calc_path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    answerable = [i for i in items if i.kind == "answerable"]
    by_table: dict[str, list[CalcItem]] = defaultdict(list)
    for i in answerable:
        by_table[i.table_key].append(i)
    scenarios: list[dict] = []
    made: list[CalcItem] = []

    def add(kind: str, turns: list[dict]) -> None:
        n = sum(s["notes"] == kind for s in scenarios) + 1
        scenarios.append({"sid": f"MT-{kind}-{n:03d}", "split": "dev", "source": "synthetic", "notes": kind,
                          "turns": turns})

    # R: two golden questions of one table, the second behind a reference phrase
    for key, group in sorted(by_table.items()):
        for a, b in list(zip(group, group[1:]))[:2]:
            q = rng.choice(REFERENCE) + b.question
            add("R", [first_turn(a), {"question": q, "expect": {"tools": [stat_call(key)], "table_key": key,
                                                                "calc_qid": b.qid}}])

    # E / X: one-fact value questions with a region or the year swapped, answers read from the Parquet file
    values = [i for i in answerable if i.type == "value" and len(i.required_facts) == 1 and i.qid not in EXCLUDE]
    for item in values:
        fact = item.required_facts[0]
        cells = Cells(item.table_key)
        region = region_of(fact)
        if region is not None:
            targets = [r for r in SIDO if r != region[1]]
            rng.shuffle(targets)
            done = 0
            for target in targets:
                cell = cells.swap(fact.source_ref, region=target)
                if cell is None:
                    continue
                short = SHORT[target]
                q = rng.choice(ELLIPSIS).format(r=short, eun=eun(short))
                calc = synthetic(item, cell, f"MT-E-{item.qid}-{cell.ref().split('|')[2]}", f"{item.question} → {q}")
                made.append(calc)
                add("E", [first_turn(item), {"question": q, "expect": {
                    "tools": [stat_call(item.table_key, regions=[target])], "table_key": item.table_key,
                    "calc_qid": calc.qid}}])
                done += 1
                if done == 3:
                    break
        year = (fact.period or "")[:4]
        if year.isdigit():
            done = 0
            for back in (1, 2, 3, 5):
                target = str(int(year) - back)
                cell = cells.swap(fact.source_ref, year=target)
                if cell is None:
                    continue
                q = rng.choice(CORRECTION).format(y=target, eun=eun(target + "년"))
                period = target + fact.period[4:]
                calc = synthetic(item, cell, f"MT-X-{item.qid}-{period}", f"{item.question} → {q}")
                made.append(calc)
                add("X", [first_turn(item), {"question": q, "expect": {
                    "tools": [stat_call(item.table_key, period_start=period, period_end=period)],
                    "table_key": item.table_key, "calc_qid": calc.qid}}])
                done += 1
                if done == 2:
                    break

    # S: a golden question of another table after the first one; the second turn must not carry its conditions
    keys = sorted(by_table)
    for n, key in enumerate(keys):
        other = keys[(n + 1) % len(keys)]
        for a, c in list(zip(by_table[key], by_table[other]))[:2]:
            add("S", [first_turn(a), {"question": c.question, "expect": {
                "tools": [{"name": "search_tables"}, stat_call(other)], "table_key": other, "calc_qid": c.qid}}])

    # C: a chart of the previous answer; M: a chart asked in the same turn
    charted = [i for i in answerable if i.type in ("trend", "compare")]
    rng.shuffle(charted)
    for item in charted[:CAP]:
        q, kind = rng.choice(CHART)
        add("C", [first_turn(item), {"question": q, "expect": {
            "tools": [{"name": "make_chart", "args": {"table_key": item.table_key, "chart_type": kind}}],
            "table_key": item.table_key}}])
    for item in charted[CAP:2 * CAP] or charted[:CAP]:
        tail, kind = rng.choice(MULTI)
        chart = {"name": "make_chart", "args": {"table_key": item.table_key, "chart_type": kind}}
        add("M", [first_turn(item, item.question + tail, extra=[chart])])

    return [AgentScenario.model_validate(s) for s in scenarios], made


def paraphrased(scenarios: list[AgentScenario], seed: int = 1) -> list[AgentScenario]:
    """The E, X and C scenarios with turn 2 reworded from PARAPHRASE (same expectations, calc_qid removed)."""
    rng = random.Random(seed)
    out = []
    for s in scenarios:
        if s.notes not in PARAPHRASE:
            continue
        data = without_calc(s).model_dump()
        turn = data["turns"][-1]
        if s.notes == "C":
            text, kind = rng.choice(PARAPHRASE["C"])
            turn["expect"]["tools"][0]["args"]["chart_type"] = kind
        else:
            args = turn["expect"]["tools"][0]["args"]
            if s.notes == "E":
                short = SHORT[args["regions"][0]]
                text = rng.choice(PARAPHRASE["E"]).format(r=short, eun=eun(short))
            else:
                year = args["period_start"][:4]
                text = rng.choice(PARAPHRASE["X"]).format(y=year, eun=eun(year + "년"))
        turn["question"] = text
        data["sid"] = f"MT-{s.notes}-P{s.sid.rsplit('-', 1)[1]}"  # MT-E-001 -> MT-E-P001 (by_kind reads the letter)
        out.append(AgentScenario.model_validate(data))
    return out


def without_calc(s: AgentScenario) -> AgentScenario:
    data = s.model_dump()
    for t in data["turns"]:
        t["expect"]["calc_qid"] = None
    return AgentScenario.model_validate(data)


def write(scenarios: list[AgentScenario], made: list[CalcItem], out: Path = OUT, out_structure: Path = OUT_STRUCTURE,
          out_calc: Path = OUT_CALC) -> None:
    dump = lambda rows: "".join(r.model_dump_json(exclude_none=True) + "\n" for r in rows)  # noqa: E731
    out.write_text(dump(scenarios), encoding="utf-8")
    out_structure.write_text(dump([without_calc(s) for s in scenarios]), encoding="utf-8")
    out_calc.write_text(dump(made), encoding="utf-8")
    OUT_PARAPHRASE.write_text(dump(paraphrased(scenarios)), encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--calc", type=Path, default=CALC)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    scenarios, made = build(args.calc, args.seed)
    write(scenarios, made)
    counts: dict[str, int] = defaultdict(int)
    for s in scenarios:
        counts[s.notes] += 1
    print(f"{len(scenarios)} scenarios ({', '.join(f'{k} {v}' for k, v in sorted(counts.items()))}), "
          f"{len(made)} synthetic answers -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
