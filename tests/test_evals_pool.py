"""T3-22: widening the judged pool for a later retriever (sheet -> a person judges -> adjudicate) and the bounds."""

import json
from types import SimpleNamespace

from evals import run_eval
from evals.builders import pool
from evals.evaluators import retrieval
from evals.schemas import RetrievalItem

BASE = {"split": "test", "source": "scenario", "question": "q"}


def items():
    return [RetrievalItem(**BASE, qid="q1", qtype="concept", required=[["a"]]),
            RetrievalItem(**BASE, qid="q2", qtype="no_answer", answerable=False, no_answer_reason="out_of_scope")]


def test_upper_bound_counts_unjudged_keys_as_gold():
    row = retrieval.score_item(items()[0], ["u", "j", "a"], judged={"j"})
    assert (row["hit@1"], row["rr"]) == (0, 1 / 3)  # lower bound: the unjudged "u" is wrong
    assert (row["hit@1_upper"], row["rr_upper"]) == (1, 1.0)  # upper bound: "u" may be an alternative
    judged = retrieval.score_item(items()[0], ["u", "j", "a"], judged={"j", "u"})
    assert judged["hit@1_upper"] == 0 and judged["rr_upper"] == 1 / 3  # once judged wrong, the bounds meet


def test_sheet_holds_only_unjudged_pairs_of_answerable_items():
    runs = [("hybrid", [{"qid": "q1", "keys": ["u", "j", "a"]}, {"qid": "q2", "keys": ["z"]}]),
            ("staged", [{"qid": "q1", "keys": ["u", "v"]}])]
    rows = pool.sheet_rows(runs, {i.qid: i for i in items()}, {"q1": {"j"}}, top=2, tables={})
    assert [(r["key"], r["found_by"]) for r in rows] == [("u", "hybrid@1+staged@1"), ("v", "staged@2")]


def test_adjudicate_adds_alternatives_only_and_skips_blank_rows():
    golden = items()
    sheet = [{"qid": "q1", "key": "u", "found_by": "hybrid@1+staged@1", "slot": "1"},
             {"qid": "q1", "key": "v", "found_by": "staged@2", "slot": "x"},
             {"qid": "q1", "key": "w", "found_by": "staged@3", "slot": ""}]
    added, changed, warnings = pool.adjudicate(sheet, golden, {"q1": {"j"}})
    assert golden[0].required == [["a", "u"]] and golden[0].label_version == 2 and golden[0].split == "test"
    assert [(r["key"], r["slot"], r["rank"]) for r in added] == [("u", "1", 1), ("v", "", 2)]  # w: not judged yet
    assert changed == ["q1"] and warnings == []


def test_adjudicate_never_relabels_an_unanswerable_item():
    golden = items()
    added, changed, warnings = pool.adjudicate([{"qid": "q2", "key": "z", "found_by": "hybrid@1", "slot": "1"}], golden, {})
    assert golden[1].required == [] and not golden[1].answerable and added == [] and changed == []
    assert warnings and "q2" in warnings[0]


def test_l1_from_run_regrades_saved_rankings_with_the_runs_tau(tmp_path):
    golden = tmp_path / "golden.jsonl"
    golden.write_text("".join(i.model_dump_json() + "\n" for i in items()), encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"retriever": "staged", "top_k": 10, "summary": {"tau": 0.5}}))
    rows = [{"qid": "q1", "split": "test", "qtype": "concept", "answerable": True, "keys": ["u", "a"], "top_score": 0.9,
             "error": None, "search_spans": []},
            {"qid": "q2", "split": "test", "qtype": "no_answer", "answerable": False, "keys": ["z"], "top_score": 0.1,
             "error": None, "search_spans": []}]
    (run / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    judged = tmp_path / "pool.csv"
    judged.write_text("qid,key,slot\nq1,a,1\n", encoding="utf-8")
    args = SimpleNamespace(golden=golden, from_run=run, retriever="ignored", k=5, tau=None, pool=judged,
                           split="test", stage="t")
    shown, summary, report = run_eval.run_l1(args)
    assert args.retriever == "staged" and summary["tau"] == 0.5 and summary["no_answer"]["tp"] == 1
    assert summary["metrics"]["all"]["hit@1"] == 0 and summary["metrics"]["all"]["hit@1_upper"] == 1
    assert "0.000 ~ 1.000" in report and "과거 실행 재채점" in report
