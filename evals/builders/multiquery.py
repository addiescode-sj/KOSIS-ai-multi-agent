"""Multi-query dev scenarios (T3-28, KOS-181): one turn that needs several tables. Data only, no LLM.

    python -m evals.builders.multiquery          # writes the files below into evals/datasets/

Four kinds, one turn each (plans/T3-28.md):

    B  pair       two calculation-golden questions on different tables joined by "그리고" / "," / "또"
                  -> search + summarize_stat per question, each graded by its own L3 item (calc_qids)
    K  combine    two one-fact value questions on different tables with the same unit + "둘을 더하면?" / "둘의 차이는?"
                  -> as B, then combine_values; its value is computed here from the golden facts
    N  no split   a golden question that compares inside one table ("A와 B", "A, B 비교") -> one search, one call
    P  paraphrase hand-written wordings with hand-labelled tables (PARAPHRASE below), for the live-search run:
                  conditions shared across parts, "와/랑" joins, connective endings, same-table pairs

B, K and N are built from golden questions verbatim, so the golden lookup search (evals.runners.multiturn_dev) finds
each part's table. That makes them a wiring check and an over-split check, not a comparison of split methods: any
splitter that cuts at the builder's joins scores the same (plans/T3-28.md, review 2). P is the comparison set; the
lookup cannot serve it, so it runs on live search and is graded on tools and tables only (no golden numbers).
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import re

from evals.builders.multiturn import alternates
from evals.schemas import DATASETS, AgentScenario, CalcItem

CALC = DATASETS / "calculation_golden.jsonl"
OUT = DATASETS / "agent_scenarios_multiquery_dev.jsonl"
OUT_STRUCTURE = DATASETS / "agent_scenarios_multiquery_dev_structure.jsonl"
OUT_PARAPHRASE = DATASETS / "agent_scenarios_multiquery_paraphrase_structure.jsonl"

JOINS = ["{a} 그리고 {b}", "{a}, {b}", "{a} 또 {b}"]
COMBINE = [("{a} 그리고 {b} 둘을 더하면?", "sum"), ("{a} 그리고 {b} 둘의 차이는?", "diff")]
SAME_TABLE_JOIN = re.compile(r"(?<=[가-힣])(?:와|과|이랑|랑|하고)\s|,")
PAIRS = 30  # B scenarios (seeded sample of all different-table pairs)

# P: written by Claude before the split rules were tuned (plans/T3-28.md Decision), reviewed by the user.
# Each part lists its acceptable tables (first = expected, rest = alternates). "op" = expected combine_values call.
# One part = the question must stay whole (same table: phase 1 answers the comparison itself).
B8000H, B8000F, B83A35 = "101_DT_1B8000H", "101_DT_1B8000F", "101_DT_1B83A35"
DENSITY, HOUSE, FOREIGN, PROJ = "101_DT_1B08024", "101_DT_1JC1502", "110_TX_11025_A000_A", "101_DT_1BPA002"
PARAPHRASE: list[dict] = [
    {"q": "2024년 경기도 출생아 수와 외국인주민 수 알려줘", "parts": [[B8000H], [FOREIGN]]},
    {"q": "2023년 제주도 1인 가구 수랑 인구밀도는?", "parts": [[HOUSE], [DENSITY]]},
    {"q": "2025년 서울 혼인 건수하고 2인 가구 수 알려줘", "parts": [[B8000H, B83A35], [HOUSE]]},
    {"q": "2020년 부산 인구밀도, 그리고 사망자 수도 알려줘", "parts": [[DENSITY], [B8000H]]},
    {"q": "2024년 서울 외국인주민은 몇 명이고 1인 가구는 몇 가구야?", "parts": [[FOREIGN], [HOUSE]]},
    {"q": "2025년 3월 전국 혼인 건수랑 2025년 경기도 1인 가구 수 알려줘", "parts": [[B83A35], [HOUSE]]},
    {"q": "2024년 경기도 외국인주민 수와 출생아 수를 더하면 몇 명이야?", "parts": [[FOREIGN], [B8000H]], "op": "sum"},
    {"q": "2024년 경기도 외국인주민 수에서 출생아 수를 빼면 몇 명이야?", "parts": [[FOREIGN], [B8000H]], "op": "diff"},
    {"q": "2023년 대구 사망자 수는? 또 같은 해 대구 인구밀도는?", "parts": [[B8000H], [DENSITY]]},
    {"q": "2022년 전국 출생아 수 및 외국인주민 수", "parts": [[B8000F, B8000H], [FOREIGN]]},
    {"q": "2024년 출생아 수와 사망자 수 알려줘", "parts": [[B8000F, B8000H]]},
    {"q": "2023년 서울 혼인 건수랑 이혼 건수는?", "parts": [[B8000H]]},
    {"q": "2025년 경기도 1인 가구와 2인 가구 수 비교해줘", "parts": [[HOUSE]]},
    {"q": "2024년 부산과 대구 조사망률 비교", "parts": [[B8000H]]},
    {"q": "중위 추계로 2030년 인구성장률과 노년부양비는 각각 얼마야?", "parts": [[PROJ]]},
    {"q": "2020년 서울과 부산의 인구밀도 차이는?", "parts": [[DENSITY]]},
]


def stat(key: str) -> dict:
    return {"name": "summarize_stat", "args": {"table_key": key}}


def part_calls(keys: list[str]) -> list[dict]:
    """Every part is searched first, then answered (the graph needs all tables to see whether they are one)."""
    return [{"name": "search_tables"} for _ in keys] + [stat(k) for k in keys]


def scenario(sid: str, kind: str, question: str, expect: dict) -> AgentScenario:
    return AgentScenario.model_validate({"sid": sid, "split": "dev", "source": "synthetic", "notes": kind,
                                         "turns": [{"question": question, "expect": expect}]})


def one_value(item: CalcItem) -> float | None:
    facts = item.required_facts
    return facts[0].value if item.type == "value" and len(facts) == 1 else None


def build(calc_path=CALC, seed: int = 0) -> list[AgentScenario]:
    rng = random.Random(seed)
    items = [CalcItem.model_validate_json(line) for line in calc_path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    answerable = [i for i in items if i.kind == "answerable"]
    alt = alternates()

    def alts(*xs: CalcItem) -> list[str]:
        keys = set().union(*(alt.get(x.qid, set()) for x in xs)) - {x.table_key for x in xs}
        return sorted(keys)

    out: list[AgentScenario] = []
    pairs = [(a, b) for a, b in itertools.permutations(answerable, 2) if a.table_key != b.table_key]
    for n, (a, b) in enumerate(rng.sample(pairs, min(PAIRS, len(pairs))), 1):
        # verbatim (a "?" left before the comma too): the golden lookup finds a part only by its exact wording
        q = rng.choice(JOINS).format(a=a.question, b=b.question)
        out.append(scenario(f"MQ-B-{n:03d}", "B", q, {"tools": part_calls([a.table_key, b.table_key]),
                                                      "table_key": b.table_key, "alt_table_keys": alts(a, b),
                                                      "calc_qids": [a.qid, b.qid]}))
    values = [i for i in answerable if one_value(i) is not None]
    n = 0
    for a, b in itertools.combinations(values, 2):
        if a.table_key == b.table_key or a.required_facts[0].unit != b.required_facts[0].unit:
            continue
        for template, op in COMBINE:
            n += 1
            va, vb = one_value(a), one_value(b)
            value = va + vb if op == "sum" else va - vb
            out.append(scenario(f"MQ-K-{n:03d}", "K", template.format(a=a.question, b=b.question), {
                "tools": [*part_calls([a.table_key, b.table_key]), {"name": "combine_values", "args": {"op": op}}],
                "table_key": b.table_key, "alt_table_keys": alts(a, b), "calc_qids": [a.qid, b.qid],
                "combine_value": value}))
    n = 0
    for item in answerable:
        if SAME_TABLE_JOIN.search(item.question):
            n += 1
            out.append(scenario(f"MQ-N-{n:03d}", "N", item.question, {
                "tools": part_calls([item.table_key]), "table_key": item.table_key, "alt_table_keys": alts(item),
                "calc_qid": item.qid}))
    return out


def paraphrased() -> list[AgentScenario]:
    out = []
    for n, p in enumerate(PARAPHRASE, 1):
        firsts = [keys[0] for keys in p["parts"]]
        tools = part_calls(firsts) + ([{"name": "combine_values", "args": {"op": p["op"]}}] if p.get("op") else [])
        alt_keys = sorted({k for keys in p["parts"] for k in keys[1:]})
        out.append(scenario(f"MQ-P-{n:03d}", "P" if len(firsts) > 1 else "PN", p["q"],
                            {"tools": tools, "table_key": firsts[-1], "alt_table_keys": alt_keys}))
    return out


def without_numbers(s: AgentScenario) -> AgentScenario:
    data = s.model_dump()
    for turn in data["turns"]:
        turn["expect"].update(calc_qid=None, calc_qids=[], combine_value=None)
    return AgentScenario.model_validate(data)


def write(rows: list[AgentScenario], path) -> None:
    path.write_text("".join(s.model_dump_json() + "\n" for s in rows), encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    rows = build(seed=args.seed)
    write(rows, OUT)
    write([without_numbers(s) for s in rows], OUT_STRUCTURE)
    write(paraphrased(), OUT_PARAPHRASE)
    kinds = {k: sum(s.notes == k for s in rows) for k in ("B", "K", "N")}
    print(f"{OUT.name}: {len(rows)} {kinds}; {OUT_PARAPHRASE.name}: {len(PARAPHRASE)}")


if __name__ == "__main__":
    main()
