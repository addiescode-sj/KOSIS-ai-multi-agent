"""T3-21: the e2e suite skips unready runs with a reason, records failures, and compares only with same-condition runs."""

import json
from argparse import Namespace
from pathlib import Path

from evals import suite

L1 = {"metrics": {"all": {"hit@1": 0.5, "hit@5": 0.7, "hit@10": 0.8, "rr": 0.6}}, "no_answer": {"f1": 0.4}}


def manifest(folder: Path, layer: str, stage: str, summary: dict, **kw) -> Path:
    folder.mkdir(parents=True)
    m = {"run_id": folder.name, "layer": layer, "stage": stage, "split": "test", "golden": {"sha256": "g"},
         "retriever": None, "top_k": None, "from_run": None, "summary": summary, **kw}
    (folder / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return folder


def opts(**kw) -> Namespace:
    return Namespace(**{"stage": "new", "llm": "mock", "baseline": None, "only": None, "interval": None, "agent": None,
                        "scenarios": None, "repeat": 1, **kw})


ALL_OK = {n: (True, "ok") for n in ("data", "v2", "chroma", "rerank", "llm", "agent")}


def test_unready_runs_are_skipped_with_the_reason_and_failures_do_not_stop_the_suite(tmp_path):
    calls = []

    def fake_main(argv):
        calls.append(argv)
        if "l2" in argv:
            raise SystemExit("golden missing")
        return manifest(tmp_path / f"run-{len(calls)}", "l1", argv[argv.index("--stage") + 1], L1, retriever="bm25-record", top_k=10)

    checks = {**ALL_OK, "chroma": (False, "Chroma localhost:8001 응답 없음")}
    s = suite.run(opts(only=["l1-bm25-record", "l1-hybrid", "l2-v1"]), main=fake_main, checks=checks)
    status = {r["name"]: (r["status"], r["reason"]) for r in s["runs"]}
    assert status["l1-hybrid"] == ("skipped", "Chroma localhost:8001 응답 없음")
    assert status["l2-v1"] == ("failed", "SystemExit: golden missing")
    assert status["l1-bm25-record"][0] == "ok" and len(calls) == 2  # the skipped run never ran
    assert calls[0][calls[0].index("--stage") + 1] == "new-l1-bm25-record"  # one stage per run: no folder clash


def test_mock_and_e2e_arguments():
    o = opts(llm="mock")
    assert suite.argv_for("e2e", ["--layer", "e2e"], "mock", o)[-6:] == ["--llm", "mock", "--interval", "0", "--retriever", "graph:staged"]
    assert suite.argv_for("e2e", ["--layer", "e2e"], "hcx", o)[-1] == "graph:staged-hcx"
    assert "--llm" not in suite.argv_for("l2-v1", ["--layer", "l2"], "mock", o)


def test_baseline_needs_the_same_conditions(tmp_path, monkeypatch):
    monkeypatch.setattr(suite, "REPORTS", tmp_path)
    manifest(tmp_path / "20261101-000000-old-l1-bm25-record", "l1", "old-l1-bm25-record", L1, retriever="bm25-record", top_k=10)
    manifest(tmp_path / "20261101-000001-old-l1-bm25-catalog", "l1", "old-l1-bm25-catalog", L1, retriever="bm25-catalog", top_k=10)
    manifest(tmp_path / "20261101-000002-old-l3", "l3", "old-l3", {"verdicts": {"all": {"PASS": 1}}})  # live, no llm field = hcx
    pool = suite.manifests(["old"])
    new_l1 = {"run_id": "x", "layer": "l1", "retriever": "bm25-record", "top_k": 10, "split": "test", "golden": {"sha256": "g"}}
    assert suite.baseline_for(new_l1, pool)[0].name.endswith("old-l1-bm25-record")
    mock_l3 = {"run_id": "y", "layer": "l3", "split": "test", "golden": {"sha256": "g"}, "llm": "mock", "top_k": None, "retriever": None}
    assert suite.baseline_for(mock_l3, pool) is None  # a mock run is never compared with an HCX run
    assert suite.baseline_for({**mock_l3, "llm": "hcx"}, pool)[0].name.endswith("old-l3")


def test_markdown_shows_checks_runs_and_deltas():
    s = {"stage": "new", "llm": "mock", "baseline": ["old"], "checks": {"chroma": [False, "응답 없음"]},
         "runs": [{"name": "l1-bm25-record", "what": "BM25", "status": "ok", "reason": "", "run": "/r/20261106-new-l1",
                   "metrics": {"Hit@5": 0.75}, "baseline": "/r/old", "base_metrics": {"Hit@5": 0.7}}]}
    md = suite.markdown(s)
    assert "| chroma | ✕ | 응답 없음 |" in md and "| Hit@5 | 0.700 | 0.750 | +0.050 |" in md and "경로 확인용" in md
    assert "report.html" in suite.html_page(s, md)
