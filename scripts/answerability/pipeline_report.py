"""Answerability classifier (KOS-193): report one evals L1 run made with the classifier IN the search graph
(ANSWER_CKPT / ANSWER_THETA: parse -> ... -> rerank1 -> answerable -> rerank2 -> gate). No model and no search: the
run's results.jsonl is read as it is, joined with the golden files by qid.

Per results row (qid, split, keys, hit@1, stage2_picked_n, stage2_error, stage2_calls, latency_ms, search_spans):
    refused   the final decision: the `gate` span's attributes.route == "refuse"; a row without a gate span falls
              back to `not keys` (counted in checks.no_gate_span)
    refuser   classifier  the `answerable` span says refused (rerank2 was skipped, gate: not_answerable)
              stage2      stage 2 ran without error and picked nothing (stage2_picked_n == 0, no stage2_error)
              no_docs     the search returned no tables
              other       none of these (only meaningful for refused rows: counted in checks)
Positive class = not answerable (golden `answerable` False; a missing field = answerable).
Output (printed and written to <run>/answerability/pipeline-<split>.json): n, n_answerable, n_unanswerable; final
(refused_right, false_refusals, missed, precision, recall, f1); by_refuser; recall_by_reason (final and per refuser,
{reason: [refused, total]}); hit1_answerable_kept / hit1_answerable_run; stage2 (called, skipped_by_classifier,
errors); latency_ms (p50 / p95: all, classifier-refused, rest); classifier (theta from the span, rows scored,
refused, top_score min / median / max per answerable / unanswerable); checks (classifier_refused_but_stage2_called
must be 0, gate_refuse_without_refuser, no_gate_span, rows_without_answerable_span). No question text is read out.

    python scripts\\answerability\\pipeline_report.py --selftest
    python scripts\\answerability\\pipeline_report.py --run evals\\reports\\<run>
    python scripts\\answerability\\pipeline_report.py --run evals\\reports\\<run> --split test --allow-test
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REFUSERS = ("classifier", "stage2", "no_docs", "other")
GOLDEN = ["eval/golden/kos192/retrieval_test_kos192.jsonl"]


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def span(r: dict, name: str) -> dict | None:
    """The first search span of that node name in a results row, or None."""
    return next((s for s in r.get("search_spans") or [] if s.get("name") == name), None)


def attrs(s: dict | None) -> dict:
    return (s or {}).get("attributes") or {}


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile (q in 0..100); None for no values."""
    v = sorted(values)
    if not v:
        return None
    pos = (len(v) - 1) * q / 100
    lo, hi = int(pos), min(int(pos) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (pos - lo), 3)


def lat(values: list[float]) -> dict:
    return {"n": len(values), "p50": percentile(values, 50), "p95": percentile(values, 95)}


def spread(values: list[float]) -> dict:
    return {"n": len(values), "min": min(values) if values else None,
            "median": percentile(values, 50) if values else None, "max": max(values) if values else None}


def row_of(r: dict, g: dict) -> dict:
    """One joined row: the final decision, who refused, and what the answerable / rerank2 spans recorded."""
    keys = r.get("keys") or []
    gate, ans, r2 = span(r, "gate"), span(r, "answerable"), span(r, "rerank2")
    refused = attrs(gate).get("route") == "refuse" if gate else not keys
    a = attrs(ans)
    if a.get("refused") is True:
        refuser = "classifier"
    elif r.get("stage2_picked_n") == 0 and not r.get("stage2_error"):
        refuser = "stage2"
    elif not keys:
        refuser = "no_docs"
    else:
        refuser = "other"
    return {"qid": r["qid"], "answerable": g.get("answerable", True) is not False,
            "reason": g.get("no_answer_reason") or "-", "hit1": bool(r.get("hit@1")), "refused": refused,
            "refuser": refuser, "no_gate": gate is None, "has_answerable": ans is not None,
            "classifier_refused": a.get("refused") is True, "top_score": a.get("top_score"),
            "theta": a.get("theta"), "n_scored": a.get("n_scored") or 0,
            "stage2_called": (r.get("stage2_calls") or 0) > 0, "stage2_error": bool(r.get("stage2_error")),
            "stage2_skipped": attrs(r2).get("skipped") == "answer_refused", "latency_ms": r.get("latency_ms")}


def gather(results: list[dict], golden: dict[str, dict], split: str) -> tuple[list[dict], int]:
    """Rows of the split joined with golden by qid; also the number of split rows with no golden entry (skipped)."""
    rows, unmatched = [], 0
    for r in results:
        if split != "all" and r.get("split") != split:
            continue
        g = golden.get(r["qid"])
        if g is None:
            unmatched += 1
            continue
        rows.append(row_of(r, g))
    return rows, unmatched


def final_block(rows: list[dict]) -> dict:
    tp = sum(r["refused"] and not r["answerable"] for r in rows)
    fp = sum(r["refused"] and r["answerable"] for r in rows)
    fn = sum(not r["refused"] and not r["answerable"] for r in rows)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {"refused_right": tp, "false_refusals": fp, "missed": fn, "precision": round(prec, 4),
            "recall": round(rec, 4), "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0}


def summarize(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["answerable"]]
    una = [r for r in rows if not r["answerable"]]
    by_refuser: dict[str, Counter] = {}
    for r in rows:
        if r["refused"]:
            by_refuser.setdefault(r["refuser"], Counter())["answerable" if r["answerable"] else "unanswerable"] += 1
    reasons = sorted({r["reason"] for r in una})
    total = Counter(r["reason"] for r in una)
    recall_by_reason = {"final": {k: [sum(r["refused"] for r in una if r["reason"] == k), total[k]] for k in reasons}}
    for who in REFUSERS:
        recall_by_reason[who] = {k: [sum(r["refused"] and r["refuser"] == who for r in una if r["reason"] == k),
                                     total[k]] for k in reasons}
    lat_all = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    lat_cls = [r["latency_ms"] for r in rows if r["latency_ms"] is not None and r["classifier_refused"]]
    lat_rest = [r["latency_ms"] for r in rows if r["latency_ms"] is not None and not r["classifier_refused"]]
    thetas = sorted({r["theta"] for r in rows if r["theta"] is not None})
    scored = [r for r in rows if r["n_scored"] > 0]
    return {
        "n": len(rows), "n_answerable": len(ans), "n_unanswerable": len(una),
        "final": final_block(rows),
        "by_refuser": {k: {"answerable": c["answerable"], "unanswerable": c["unanswerable"]}
                       for k, c in sorted(by_refuser.items(), key=lambda kv: REFUSERS.index(kv[0]))},
        "recall_by_reason": recall_by_reason,
        "hit1_answerable_kept": round(sum(r["hit1"] and not r["refused"] for r in ans) / len(ans), 4) if ans else None,
        "hit1_answerable_run": round(sum(r["hit1"] for r in ans) / len(ans), 4) if ans else None,
        "stage2": {"called": sum(r["stage2_called"] for r in rows),
                   "skipped_by_classifier": sum(r["stage2_skipped"] for r in rows),
                   "errors": sum(r["stage2_error"] for r in rows)},
        "latency_ms": {"all": lat(lat_all), "classifier_refused": lat(lat_cls), "rest": lat(lat_rest)},
        "classifier": {"theta": thetas[0] if len(thetas) == 1 else (thetas or None), "n_scored": len(scored),
                       "refused": sum(r["classifier_refused"] for r in rows),
                       "top_score": {"answerable": spread([r["top_score"] for r in scored if r["answerable"]
                                                           and r["top_score"] is not None]),
                                     "unanswerable": spread([r["top_score"] for r in scored if not r["answerable"]
                                                             and r["top_score"] is not None])}},
        "checks": {"classifier_refused_but_stage2_called": sum(r["classifier_refused"] and r["stage2_called"]
                                                               for r in rows),
                   "gate_refuse_without_refuser": sum(r["refused"] and r["refuser"] == "other" for r in rows),
                   "no_gate_span": sum(r["no_gate"] for r in rows),
                   "rows_without_answerable_span": sum(not r["has_answerable"] for r in rows)},
    }


def selftest() -> None:
    def spans(route=None, refused=None, top=None, skipped=None, theta=-1.0):
        out = []
        if refused is not None:
            out.append({"name": "answerable", "attributes": {k: v for k, v in (
                ("top_score", top), ("refused", refused), ("theta", theta), ("n_scored", 5)) if v is not None}})
        if skipped:
            out.append({"name": "rerank2", "attributes": {"skipped": skipped}})
        if route:
            out.append({"name": "gate", "attributes": {"route": route}})
        return out

    res = [
        # classifier refuses an unanswerable row (rerank2 skipped, no stage 2 call)
        {"qid": "c1", "split": "dev", "keys": ["X"], "hit@1": 0, "stage2_picked_n": None, "stage2_calls": None,
         "latency_ms": 100.0, "search_spans": spans("refuse", True, -3.0, "answer_refused")},
        # classifier refuses an answerable row (false refusal) that still had hit@1
        {"qid": "c2", "split": "dev", "keys": ["A"], "hit@1": 1, "stage2_picked_n": None, "stage2_calls": None,
         "latency_ms": 120.0, "search_spans": spans("refuse", True, -2.0, "answer_refused")},
        # classifier passes, stage 2 picks nothing: refused by stage 2
        {"qid": "s1", "split": "dev", "keys": ["Y"], "hit@1": 0, "stage2_picked_n": 0, "stage2_calls": 1,
         "latency_ms": 900.0, "search_spans": spans("refuse", False, 1.0)},
        # passes both, answerable, hit
        {"qid": "k1", "split": "dev", "keys": ["A"], "hit@1": 1, "stage2_picked_n": 1, "stage2_calls": 1,
         "latency_ms": 1000.0, "search_spans": spans("found", False, 2.0)},
        # passes both, unanswerable: missed; stage 2 failed (an error is not a refusal)
        {"qid": "m1", "split": "dev", "keys": ["Z"], "hit@1": 0, "stage2_picked_n": 0, "stage2_calls": 1,
         "stage2_error": "boom", "latency_ms": 800.0, "search_spans": spans("found", False, 0.5)},
        # no tables at all: refused by the gate, no gate span recorded -> fallback; no answerable span either
        {"qid": "n1", "split": "dev", "keys": [], "hit@1": 0, "stage2_picked_n": None, "latency_ms": 50.0,
         "search_spans": []},
        # gate refused but nobody did (inconsistent): "other"; and a classifier refusal with a stage 2 call (bug)
        {"qid": "o1", "split": "dev", "keys": ["A"], "hit@1": 1, "stage2_picked_n": 2, "stage2_calls": 1,
         "latency_ms": 700.0, "search_spans": spans("refuse", False, 3.0)},
        {"qid": "b1", "split": "dev", "keys": ["X"], "hit@1": 0, "stage2_picked_n": 1, "stage2_calls": 1,
         "latency_ms": 600.0, "search_spans": spans("refuse", True, -4.0)},
        # other split / not in golden: left out
        {"qid": "t1", "split": "test", "keys": ["A"], "hit@1": 1, "stage2_picked_n": 1, "stage2_calls": 1,
         "latency_ms": 10.0, "search_spans": spans("found", False, 2.0)},
        {"qid": "zz", "split": "dev", "keys": ["A"], "latency_ms": 10.0, "search_spans": []},
    ]
    gold = {"c1": {"answerable": False, "no_answer_reason": "no_period"},
            "c2": {"answerable": True},
            "s1": {"answerable": False, "no_answer_reason": "no_item"},
            "k1": {},  # missing answerable = answerable
            "m1": {"answerable": False, "no_answer_reason": "no_item"},
            "n1": {"answerable": False, "no_answer_reason": "out_of_scope"},
            "o1": {"answerable": True},
            "b1": {"answerable": False, "no_answer_reason": "no_period"},
            "t1": {"answerable": True}}
    rows, unmatched = gather(res, gold, "dev")
    assert [r["qid"] for r in rows] == ["c1", "c2", "s1", "k1", "m1", "n1", "o1", "b1"] and unmatched == 1
    by = {r["qid"]: r for r in rows}
    assert [by[q]["refuser"] for q in ("c1", "c2", "s1", "k1", "m1", "n1", "o1", "b1")] == \
        ["classifier", "classifier", "stage2", "other", "other", "no_docs", "other", "classifier"]
    assert by["n1"]["refused"] and by["n1"]["no_gate"] and not by["m1"]["refused"]
    s = summarize(rows)
    assert (s["n"], s["n_answerable"], s["n_unanswerable"]) == (8, 3, 5)
    # unanswerable c1 s1 m1 n1 b1: refused c1 s1 n1 b1, missed m1; false refusals c2 o1
    assert s["final"] == {"refused_right": 4, "false_refusals": 2, "missed": 1, "precision": 0.6667, "recall": 0.8,
                          "f1": round(2 * (4 / 6) * 0.8 / (4 / 6 + 0.8), 4)}, s["final"]
    assert s["by_refuser"] == {"classifier": {"answerable": 1, "unanswerable": 2},
                               "stage2": {"answerable": 0, "unanswerable": 1},
                               "no_docs": {"answerable": 0, "unanswerable": 1},
                               "other": {"answerable": 1, "unanswerable": 0}}, s["by_refuser"]
    rr = s["recall_by_reason"]
    assert rr["final"] == {"no_item": [1, 2], "no_period": [2, 2], "out_of_scope": [1, 1]}, rr
    assert rr["classifier"] == {"no_item": [0, 2], "no_period": [2, 2], "out_of_scope": [0, 1]}
    assert rr["stage2"]["no_item"] == [1, 2] and rr["no_docs"]["out_of_scope"] == [1, 1]
    # answerable c2 (hit, refused), k1 (hit, kept), o1 (hit, refused)
    assert s["hit1_answerable_kept"] == round(1 / 3, 4) and s["hit1_answerable_run"] == 1.0
    assert s["stage2"] == {"called": 5, "skipped_by_classifier": 2, "errors": 1}, s["stage2"]
    assert s["latency_ms"]["classifier_refused"] == {"n": 3, "p50": 120.0, "p95": 552.0}, s["latency_ms"]
    assert s["latency_ms"]["all"]["n"] == 8 and s["latency_ms"]["rest"]["n"] == 5
    c = s["classifier"]
    assert c["theta"] == -1.0 and c["n_scored"] == 7 and c["refused"] == 3
    assert c["top_score"]["answerable"] == {"n": 3, "min": -2.0, "median": 2.0, "max": 3.0}, c["top_score"]
    assert c["top_score"]["unanswerable"] == {"n": 4, "min": -4.0, "median": -1.25, "max": 1.0}, c["top_score"]
    assert s["checks"] == {"classifier_refused_but_stage2_called": 1, "gate_refuse_without_refuser": 1,
                           "no_gate_span": 1, "rows_without_answerable_span": 1}, s["checks"]
    # split filter: test only / all
    t_rows, t_un = gather(res, gold, "test")
    assert [r["qid"] for r in t_rows] == ["t1"] and t_un == 0
    a_rows, a_un = gather(res, gold, "all")
    assert len(a_rows) == 9 and a_un == 1
    assert summarize([])["final"]["f1"] == 0.0 and summarize([])["hit1_answerable_kept"] is None
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5 and percentile([], 50) is None
    print("selftest ok")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--run", type=Path, help="evals/reports/<run> folder with results.jsonl (classifier in the graph)")
    p.add_argument("--golden", nargs="+", default=GOLDEN,
                   help="golden files joined by qid (fields: answerable, no_answer_reason; missing files are skipped)")
    p.add_argument("--split", choices=("dev", "test", "all"), default="dev")
    p.add_argument("--allow-test", action="store_true")
    a = p.parse_args()
    if a.selftest:
        selftest()
        return
    if not a.run:
        raise SystemExit("need --run")
    if a.split != "dev" and not a.allow_test:
        raise SystemExit("--split test/all reads test questions: add --allow-test")
    golden: dict[str, dict] = {}
    for gp in a.golden:
        if Path(gp).is_file():
            for x in read_jsonl(Path(gp)):
                golden[x["qid"]] = x
        else:
            print(f"golden file not found, skipped: {gp}", file=sys.stderr)
    rows, unmatched = gather(read_jsonl(a.run / "results.jsonl"), golden, a.split)
    if not rows:
        raise SystemExit(f"{a.run}: no results row matched the golden files and split")
    summary = {"run": str(a.run), "split": a.split, "golden": list(a.golden), "unmatched_rows": unmatched,
               **summarize(rows)}
    out_dir = a.run / "answerability"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"pipeline-{a.split}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"-> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
