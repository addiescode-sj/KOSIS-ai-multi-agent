"""evals.evaluators.gate (KOS-193): the graph's real refusals; same numbers as scripts/answerability/pipeline_report.py;
the gate line in the L1 report.md and gate_report.md next to it."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from evals.evaluators import gate
from evals.tasks import l1_retrieval

ROOT = Path(__file__).resolve().parents[1]


def pipeline_report():
    path = ROOT / "scripts" / "answerability" / "pipeline_report.py"
    spec = importlib.util.spec_from_file_location("pipeline_report", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_summarize_final_refusers_and_lists():
    s = gate.summarize([gate.row_of(r) for r in gate.fixture()])
    assert s["final"]["refused_right"] == 4 and s["final"]["false_refusals"] == 2 and s["final"]["missed"] == 1
    assert s["by_refuser"]["classifier"] == {"answerable": 1, "unanswerable": 2}
    assert s["by_refuser"]["no_docs"] == {"answerable": 0, "unanswerable": 1}
    assert [x["qid"] for x in s["false_refusals"]] == ["c2", "o1"]
    assert s["missed"] == [{"qid": "m1", "reason": "no_item", "top_score": 0.5}]
    assert s["has_gate"] and s["has_classifier"]


def test_same_numbers_as_pipeline_report():
    pr = pipeline_report()
    res = gate.fixture()
    golden = {r["qid"]: {k: r[k] for k in ("answerable", "no_answer_reason") if k in r} for r in res}
    rows, unmatched = pr.gather(res, golden, "dev")
    want = pr.summarize(rows)
    got = gate.summarize([gate.row_of(r) for r in res])
    assert unmatched == 0
    for key, value in want.items():
        assert got[key] == value, key


def summary(**extra) -> dict:
    metrics = {"all": {"n": 2, "hit@1": 0.5}}
    no_answer = {"precision": 0.5, "recall": 0.25, "f1": 1 / 3, "tp": 1, "fp": 1, "fn": 3}
    return {"tau": 0.42, "metrics": metrics, "no_answer": no_answer, "errors": 0,
            "refusal_by_reason": {"no_item": {"n": 2, "refused": 1, "rate": 0.5}}, **extra}


ARGS = SimpleNamespace(stage="KOS-193-test", retriever="graph:x", k=10, split="dev", from_run=None)


def test_report_gate_line_after_tau_line():
    plain = l1_retrieval.report(ARGS, summary())
    assert "gate" not in plain
    g = gate.summarize([gate.row_of(r) for r in gate.fixture()])
    with_gate = l1_retrieval.report(ARGS, summary(gate=g))
    lines, before = with_gate.splitlines(), plain.splitlines()
    tau = next(i for i, x in enumerate(lines) if x.startswith("- 답 없음 (tau="))
    assert lines[tau] == next(x for x in before if x.startswith("- 답 없음 (tau="))
    assert lines[tau + 1] == gate.summary_line(g)
    assert lines[:tau + 1] + lines[tau + 2:] == before


def test_extra_files():
    assert l1_retrieval.extra_files(ARGS, [], summary()) == {}
    g = gate.summarize([gate.row_of(r) for r in gate.fixture()])
    files = l1_retrieval.extra_files(ARGS, [], summary(gate=g))
    assert list(files) == ["gate_report.md"]
    md = files["gate_report.md"]
    assert md.startswith("# 실제 거절 내역 (gate) — KOS-193-test") and "retriever `graph:x`" in md
    assert "참고: τ 규칙(파이프라인에는 없음) tau=0.42" in md
