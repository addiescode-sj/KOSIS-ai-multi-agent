"""T3-47 quality gate of the e2e golden set (plans/golden-quality-gate.md). No LLM, no search.

    python -m evals.golden_gate              # evals/datasets/e2e_golden.jsonl -> e2e_golden.gate.json + review CSVs
    python -m evals.builders.e2e_golden      # runs this gate at the end of every build

Gate policy (plans/T3-47.md): integrity checks BLOCK (exit 1) — unintended exact duplicates, leakage into the test split,
gold table / cell validity (ambiguous cells included), stale references. Distribution checks WARN — required-scenario
coverage, minimum sample per type, TVD against the user-question proxy, gold labels waiting for review.
Thresholds live in GATES and must match the document's section 1.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from evals.schemas import DATASETS, ROOT, TABLES, AgentScenario, CalcItem, load_jsonl

GOLDEN = DATASETS / "e2e_golden.jsonl"
CALC = DATASETS / "e2e_golden_calc.jsonl"
OUT = DATASETS / "e2e_golden.gate.json"
PROXY = DATASETS / "retrieval_golden.jsonl"  # human-written questions: the user-distribution proxy until service logs
GATES = {"coverage": 1.0, "min_per_type": 20, "tvd": 0.10, "exact_dup": 0, "test_leakage": 0, "invalid_refs": 0, "stale": 0}
MAX_ROWS = 2_000_000  # cell-id uniqueness is checked on tables up to this size (bigger: reported, not read)
TOTALS = {"계", "합계", "총계", "전체", "소계", "전국", "총인구", "Total"}


# ---------------------------------------------------------------- shared helpers (the builder uses them too)
def norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", text).lower())


def _shape(text: str) -> str:
    """A follow-up's wording with its slots blanked: years, 시도 names and their particles."""
    from kosis_rag.shared.vocab import SIDO

    text = re.sub(r"\d{4}\s*년(?:도)?\s*(?:은|는)?", "{y}", text)
    for alias in sorted({a for n, al in SIDO.items() for a in (n, *al)}, key=len, reverse=True):
        text = re.sub(re.escape(alias) + r"\s*(?:은|는)?", "{r}", text)
    return norm(text.replace("{y}", "YYYY").replace("{r}", "RRRR"))


def tuned_shapes() -> set[str]:
    """Follow-up wordings the context rules were tuned on (T3-27 builder templates, the LLM reader's examples)."""
    from kosis_agent.graph.context import LLM_EXAMPLES

    from evals.builders import multiturn as mt

    shapes = {_shape(t.format(r="부산", y="2023", eun="은")) for t in [*mt.ELLIPSIS, *mt.CORRECTION]}
    shapes |= {_shape(q) for q, _ in mt.CHART} | {_shape(q) for q, _ in mt.MULTI} | {_shape(q) for q in mt.AUTO_C}
    shapes |= {_shape(q) for _, q, _ in LLM_EXAMPLES}
    return shapes


def tuned(question: str, shapes: set[str]) -> bool:
    """A follow-up worded like a rule-tuning template (or a reference phrase the rules key on, "그 표에서 …")."""
    from evals.builders import multiturn as mt

    return _shape(question) in shapes or any(question.startswith(r) for r in mt.REFERENCE)


def degenerate(item: CalcItem) -> bool:
    """No discriminating power: a comparison of two zeros, a trend whose ends are equal."""
    cells = [f for f in item.required_facts if f.source_ref]
    if item.type == "compare" and cells and all((f.value or 0) == 0 for f in cells):
        return True
    return item.type == "trend" and len(cells) >= 2 and cells[0].value == cells[-1].value


def table_rows() -> dict[str, int]:
    with (ROOT / "data" / "tables.csv").open(encoding="utf-8-sig", newline="") as f:
        return {r["key"]: int(r.get("rows") or 0) for r in csv.DictReader(f)}


def ambiguous_refs(items: Iterable[CalcItem], max_rows: int = MAX_ROWS) -> tuple[set[str], list[str]]:
    """(source_refs that name more than one cell of their table, tables too big to check). A gold fact on such a ref
    has no single right value (101_DT_1PD1006, 2026-11-20)."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    from kosis_ai.schemas import Cell

    rows = table_rows()
    wanted: dict[str, set[str]] = defaultdict(set)
    for it in items:
        for f in it.required_facts + it.aux_facts:
            if f.source_ref:
                wanted[it.table_key].add(f.source_ref)
    bad, skipped = set(), []
    for key, refs in wanted.items():
        path = ROOT / "data" / "values" / f"{key}.parquet"
        if rows.get(key, 0) > max_rows or not path.exists():
            skipped.append(key)
            continue
        table = pq.read_table(path)
        table = table.filter(pc.is_in(table["period"], value_set=pa.array(sorted({r.rsplit("|", 1)[1] for r in refs}))))
        seen = Counter(Cell.from_row({k: (None if v != v else v) for k, v in r.items()}).ref() for r in table.to_pylist())
        bad |= {r for r in refs if seen[r] > 1}
    return bad, sorted(skipped)


# ---------------------------------------------------------------- required scenarios (coverage)
def _labels(t):
    return t.expect.labels


REQUIRED = {
    "qtype: value": lambda s, t: _labels(t).qtype == "value",
    "qtype: compare": lambda s, t: _labels(t).qtype == "compare",
    "qtype: trend": lambda s, t: _labels(t).qtype == "trend",
    "qtype: share": lambda s, t: _labels(t).qtype == "share",
    "qtype: extreme": lambda s, t: _labels(t).qtype == "extreme",
    "formula: diff": lambda s, t: "diff" in (_labels(t).formula or []),
    "formula: pp_diff": lambda s, t: "pp_diff" in (_labels(t).formula or []),
    "formula: pct_change": lambda s, t: "pct_change" in (_labels(t).formula or []),
    "formula: ratio": lambda s, t: "ratio" in (_labels(t).formula or []),
    "formula: cagr": lambda s, t: "cagr" in (_labels(t).formula or []),
    "formula: share": lambda s, t: "share" in (_labels(t).formula or []),
    "formula: per": lambda s, t: "per" in (_labels(t).formula or []),
    "rewrite: 시도 이월": lambda s, t: s.notes in ("E", "XE"),
    "rewrite: 연도 정정": lambda s, t: s.notes in ("X", "XE", "XY"),
    "rewrite: 상대 연도": lambda s, t: s.notes == "Y",
    "rewrite: 지시어": lambda s, t: s.notes == "R",
    "rewrite: 차트만": lambda s, t: s.notes == "C",
    "rewrite: 주제 전환": lambda s, t: s.notes == "S",
    "rewrite: 멀티쿼리 분해": lambda s, t: len(_labels(t).sub_questions or []) > 1,
    "rewrite: 멀티쿼리 표 사이 계산": lambda s, t: _labels(t).op is not None,
    "rewrite: 3턴 이상": lambda s, t: len(s.turns) >= 3,
    "rewrite: 규칙 판단 보류 후속": lambda s, t: _labels(t).reading == "defer",
    "classify: no_period": lambda s, t: "no_period" in (_labels(t).refusal_reasons or []) and _labels(t).judge_rule != "partial_range",
    "classify: missing_value": lambda s, t: "missing_value" in (_labels(t).refusal_reasons or []),
    "classify: 범위 밖": lambda s, t: "out_of_scope" in (_labels(t).refusal_reasons or []),
    "classify: causal": lambda s, t: _labels(t).judge_rule == "causal",
    "classify: not_aggregatable": lambda s, t: _labels(t).judge_rule == "not_aggregatable",
    "classify: partial_range / no_cell": lambda s, t: _labels(t).judge_rule in ("partial_range", "no_cell", "unit_mismatch"),
    "classify: evaluative 되묻기": lambda s, t: _labels(t).judge_rule == "evaluative",
    "classify: same_name / vague_item 되묻기": lambda s, t: _labels(t).judge_rule in ("same_name", "vague_item"),
    "classify: 기본값": lambda s, t: bool(_labels(t).default_applied),
}


def coverage(scenarios: list[AgentScenario]) -> dict:
    counts = {name: sum(1 for s in scenarios if s.turns[-1].expect.labels and fn(s, s.turns[-1])) for name, fn in REQUIRED.items()}
    present = sum(v > 0 for v in counts.values())
    enough = sum(v >= GATES["min_per_type"] for v in counts.values())
    return {"types": counts, "present": present, "required": len(counts), "coverage": present / len(counts),
            "min_ok": enough, "min_rate": enough / len(counts),
            "missing": [k for k, v in counts.items() if v == 0],
            "below_min": {k: v for k, v in counts.items() if 0 < v < GATES["min_per_type"]}}


# ---------------------------------------------------------------- distribution (TVD against the proxy)
PROXY_BUCKET = {"exact_name": "simple", "concept": "simple", "sibling": "simple", "multi_condition": "multi_condition",
                "period_cycle": "period", "multi_table": "multi_table", "ambiguous": "ambiguous", "no_answer": "no_answer"}


def bucket(s: AgentScenario, calc: dict[str, CalcItem]) -> str:
    t = s.turns[-1]
    lab = t.expect.labels
    if lab.outcome == "clarify":
        return "ambiguous"
    if lab.outcome == "refused":
        return "no_answer"
    if len(lab.sub_questions or []) > 1:
        return "multi_table"
    if lab.qtype == "trend" or s.notes in ("cagr", "X", "Y", "XE", "XY"):
        return "period"
    item = calc.get(t.expect.calc_qid or "")
    dims = [v for k, v in ((item.required_facts[0].dims or {}) if item and item.required_facts else {}).items()
            if k != "항목" and v.strip() not in TOTALS and not v.strip().endswith("계")]
    return "multi_condition" if len(dims) >= 2 else "simple"


def tvd(scenarios: list[AgentScenario], calc: dict[str, CalcItem], proxy: Path = PROXY) -> dict:
    if not proxy.exists():
        return {"tvd": None, "note": f"{proxy.name} 없음"}
    q = Counter(PROXY_BUCKET.get(json.loads(l)["qtype"], "simple") for l in proxy.read_text(encoding="utf-8").splitlines() if l.strip())
    p = Counter(bucket(s, calc) for s in scenarios)
    keys = sorted(set(p) | set(q))
    P, Q = ({k: c[k] / sum(c.values()) for k in keys} for c in (p, q))
    return {"tvd": round(0.5 * sum(abs(P[k] - Q[k]) for k in keys), 3), "golden": {k: round(P[k], 3) for k in keys},
            "proxy": {k: round(Q[k], 3) for k in keys}, "proxy_n": sum(q.values()),
            "note": "대리 분포: 사람이 쓴 검색 골든 질문 (서비스 로그가 쌓이면 교체)"}


# ---------------------------------------------------------------- duplicates
def duplicates(scenarios: list[AgentScenario]) -> dict:
    """Unintended = the same question graded as the last turn of two scenarios. A first turn reused as the context of
    a follow-up conversation is intended (counted apart)."""
    graded = defaultdict(list)
    for s in scenarios:
        last = s.turns[-1]
        # a turn that leans on the previous one ("꺾은선 그래프로 보여줘", "부산은?") is the same case only in the same conversation
        own = (last.expect.labels is None or last.expect.labels.reading in (None, "new"))
        key = norm(last.question) if own else "|".join(norm(t.question) for t in s.turns)
        graded[hashlib.sha256(key.encode()).hexdigest()].append(s.sid)
    unintended = [v for v in graded.values() if len(v) > 1]
    every = Counter(norm(t.question) for s in scenarios for t in s.turns)
    return {"unintended": sum(len(v) - 1 for v in unintended), "unintended_rate": round(sum(len(v) - 1 for v in unintended) / max(len(scenarios), 1), 4),
            "examples": unintended[:5], "turn_level": sum(n - 1 for n in every.values() if n > 1)}


def near_duplicates(scenarios: list[AgentScenario], threshold: float = 0.85) -> list[tuple]:
    """Candidate pairs for a person (char 2-4gram TF-IDF cosine, local): (score, sid a, question a, sid b, question b)."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    last = [(s.sid, s.turns[-1].question) for s in scenarios]
    if len(last) < 2:
        return []
    sim = cosine_similarity(TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4)).fit_transform([q for _, q in last]))
    out = [(round(float(sim[a, b]), 3), *last[a], *last[b]) for a in range(len(last)) for b in range(a + 1, len(last))
           if sim[a, b] >= threshold and norm(last[a][1]) != norm(last[b][1])]
    return sorted(out, reverse=True)


# ---------------------------------------------------------------- leakage
def leakage(scenarios: list[AgentScenario]) -> dict:
    shapes = tuned_shapes()
    test_tuned = [(s.sid, t.question) for s in scenarios if s.split == "test" for i, t in enumerate(s.turns) if i and tuned(t.question, shapes)]
    source = "".join(f.read_text(encoding="utf-8", errors="ignore") for base in ("src", "backend", "vendor/phase2/src", "vendor/phase1/kosis_chat")
                     for f in (ROOT / base).rglob("*") if f.is_file() and f.suffix in (".py", ".md", ".txt", ".json", ".yaml", ".yml"))
    src = norm(source)
    in_code = [s.sid for s in scenarios for t in s.turns if len(norm(t.question)) >= 12 and norm(t.question) in src]
    runner = (ROOT / "evals" / "runners" / "agent.py").read_text(encoding="utf-8")
    payloads = sorted(set(re.findall(r"graph\.invoke\((\{[^}]*\})", runner)))
    answer_leak = [p for p in payloads if re.search(r"table|calc|expect|gold", p)]
    reads_golden = [str(f.relative_to(ROOT)) for base in ("src", "backend") for f in (ROOT / base).rglob("*.py")
                    if re.search(r"evals/datasets|e2e_golden|calc_qid|calculation_golden", f.read_text(encoding="utf-8"))]
    return {"test_tuned_followups": len(test_tuned), "test_tuned_examples": test_tuned[:5], "questions_in_code": len(in_code),
            "answer_leak_payloads": answer_leak, "serving_code_reading_golden": reads_golden,
            "splits": dict(Counter(s.split for s in scenarios)),
            "verified": len(test_tuned) + len(in_code) + len(answer_leak) + len(reads_golden)}


# ---------------------------------------------------------------- gold validity, staleness, labels
def validity(scenarios: list[AgentScenario], calc: dict[str, CalcItem], check_cells: bool = True) -> dict:
    from evals.evaluators import calculation

    meta = {json.loads(l)["info"]["key"] for l in TABLES.read_text(encoding="utf-8").splitlines() if l.strip()}
    refs = [t.expect.table_key for s in scenarios for t in s.turns if t.expect.table_key]
    unknown = sorted({k for k in refs if k not in meta or not (ROOT / "data" / "values" / f"{k}.parquet").exists()})
    missing_calc = sorted({q for s in scenarios for t in s.turns for q in [t.expect.calc_qid, *t.expect.calc_qids] if q and q not in calc})
    stale, wrong_cells, checked, sha = [], [], 0, {}
    for it in calc.values():
        path = ROOT / "data" / "values" / f"{it.table_key}.parquet"
        if not path.exists():
            continue
        if it.table_key not in sha:
            sha[it.table_key] = hashlib.sha256(path.read_bytes()).hexdigest()
        if it.table_sha256 != sha[it.table_key]:
            stale.append(it.qid)
        if not check_cells:
            continue
        refs_ = frozenset(f.source_ref for f in it.required_facts + it.aux_facts if f.source_ref)
        cells = calculation.read_cells(path, refs_) if refs_ else {}
        for f in it.required_facts + it.aux_facts:
            if not f.source_ref:
                continue
            checked += 1
            c = cells.get(f.source_ref)
            if c is None or (f.value is not None and not calculation.close(f.value, c.value, None)):
                wrong_cells.append(it.qid)
    ambiguous, too_big = ambiguous_refs(calc.values()) if check_cells else (set(), [])
    amb_items = sorted({it.qid for it in calc.values() for f in it.required_facts + it.aux_facts if f.source_ref in ambiguous})
    return {"table_refs": len(refs), "unknown_tables": unknown, "missing_calc_items": missing_calc,
            "cells_checked": checked, "wrong_cells": sorted(set(wrong_cells)), "ambiguous_cell_items": amb_items,
            "not_checked_large_tables": too_big, "stale_items": stale,
            "invalid": len(unknown) + len(missing_calc) + len(set(wrong_cells) | set(amb_items))}


def label_review(scenarios: list[AgentScenario], calc: dict[str, CalcItem]) -> dict:
    review = Counter((t.expect.labels.review or "none") for s in scenarios for t in s.turns if t.expect.labels)
    degen = sorted({s.sid for s in scenarios for t in s.turns if (it := calc.get(t.expect.calc_qid or "")) and degenerate(it)})
    return {"review": dict(review), "needs_review": review.get("needs_review", 0), "degenerate": degen}


# ---------------------------------------------------------------- the gate
def run(golden: Path = GOLDEN, calc_path: Path = CALC, check_cells: bool = True) -> dict:
    scenarios = load_jsonl(golden, AgentScenario)
    calc = {i.qid: i for i in load_jsonl(calc_path, CalcItem)}
    cov, dist, dup = coverage(scenarios), tvd(scenarios, calc), duplicates(scenarios)
    leak, valid, lab = leakage(scenarios), validity(scenarios, calc, check_cells), label_review(scenarios, calc)
    checks = [  # (name, blocking, ok, value, threshold)
        ("필수 시나리오 Coverage", False, cov["coverage"] >= GATES["coverage"], f"{cov['coverage']:.1%} ({cov['present']}/{cov['required']})", "100%"),
        ("유형별 최소 표본", False, cov["min_ok"] == cov["required"], f"{cov['min_ok']}/{cov['required']} 유형", f"≥ {GATES['min_per_type']}"),
        ("사용자 분포 TVD (대리)", False, dist["tvd"] is not None and dist["tvd"] <= GATES["tvd"], str(dist["tvd"]), f"≤ {GATES['tvd']}"),
        ("미검수 라벨 (needs_review)", False, lab["needs_review"] == 0, str(lab["needs_review"]), "0"),
        ("퇴화 문항", False, not lab["degenerate"], str(len(lab["degenerate"])), "0"),
        ("의도하지 않은 Exact Duplicate", True, dup["unintended"] <= GATES["exact_dup"], f"{dup['unintended']} ({dup['unintended_rate']:.1%})", "0"),
        ("확인된 test 누출", True, leak["verified"] <= GATES["test_leakage"], str(leak["verified"]), "0"),
        ("정답 ID·셀 유효성", True, valid["invalid"] <= GATES["invalid_refs"], str(valid["invalid"]), "0"),
        ("Stale 참조", True, len(valid["stale_items"]) <= GATES["stale"], str(len(valid["stale_items"])), "0"),
    ]
    near = near_duplicates(scenarios)
    blocked = [n for n, block, ok, *_ in checks if block and not ok]
    return {"golden_sha256": hashlib.sha256(golden.read_bytes()).hexdigest(), "scenarios": len(scenarios),
            "checks": [{"name": n, "blocking": b, "ok": ok, "value": v, "threshold": th} for n, b, ok, v, th in checks],
            "blocked": blocked, "passed": not blocked, "coverage": cov, "tvd": dist, "duplicates": dup,
            "near_duplicate_candidates": len(near), "leakage": leak, "validity": valid, "labels": lab, "_near": near}


def table(result: dict) -> str:
    lines = ["| 검사 | 막음/경고 | 결과 | 기준 | 판정 |", "| --- | --- | --- | --- | --- |"]
    for c in result["checks"]:
        lines.append(f"| {c['name']} | {'막음' if c['blocking'] else '경고'} | {c['value']} | {c['threshold']} | {'✓' if c['ok'] else '✗'} |")
    return "\n".join(lines)


def write(result: dict, out: Path = OUT) -> None:
    near = result.pop("_near")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    with out.with_name("e2e_golden.near_duplicates.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["score", "sid_a", "question_a", "sid_b", "question_b", "verdict(같은 능력이면 duplicate)"])
        w.writerows([*row, ""] for row in near)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--golden", type=Path, default=GOLDEN)
    parser.add_argument("--calc", type=Path, default=CALC)
    parser.add_argument("--no-cells", action="store_true", help="skip reading Parquet cells (fast, ids and hashes only)")
    args = parser.parse_args(argv)
    result = run(args.golden, args.calc, not args.no_cells)
    print(table(result))
    write(result)
    print(f"-> {OUT}" + ("" if result["passed"] else f"  (막음: {', '.join(result['blocked'])})"))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
