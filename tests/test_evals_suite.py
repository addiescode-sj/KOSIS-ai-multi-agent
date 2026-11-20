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


ALL_OK = {n: (True, "ok") for n in ("data", "v2", "vectors", "rerank", "llm", "agent", "judge", "e2e_golden")}


def test_unready_runs_are_skipped_with_the_reason_and_failures_do_not_stop_the_suite(tmp_path):
    calls = []

    def fake_main(argv):
        calls.append(argv)
        if "l2" in argv:
            raise SystemExit("golden missing")
        return manifest(tmp_path / f"run-{len(calls)}", "l1", argv[argv.index("--stage") + 1], L1, retriever="bm25-record", top_k=10)

    checks = {**ALL_OK, "vectors": (False, "PostgreSQL localhost:5432 응답 없음")}
    s = suite.run(opts(only=["l1-bm25-record", "l1-hybrid", "l2-v1"]), main=fake_main, checks=checks)
    status = {r["name"]: (r["status"], r["reason"]) for r in s["runs"]}
    assert status["l1-hybrid"] == ("skipped", "PostgreSQL localhost:5432 응답 없음")
    assert status["l2-v1"] == ("failed", "SystemExit: golden missing")
    assert status["l1-bm25-record"][0] == "ok" and len(calls) == 2  # the skipped run never ran
    assert calls[0][calls[0].index("--stage") + 1] == "new-l1-bm25-record"  # one stage per run: no folder clash


def test_mock_and_e2e_arguments():
    o = opts(llm="mock")
    # T3-32: e2e is the service graph, its search settings are the shell's: no --retriever preset any more
    assert suite.argv_for("e2e", ["--layer", "e2e"], "mock", o)[-4:] == ["--llm", "mock", "--interval", "0"]
    assert "--retriever" not in suite.argv_for("e2e", ["--layer", "e2e"], "hcx", o)
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


KEY = {"golden_sha256": "g", "judge": "gpt-4.1-mini"}
RAGAS = {"n": 90, "key": KEY, "gate": {"faithfulness": 0.80, "e2e_pass_rate": 0.5}, "source_run": "e",
         "metrics": {"faithfulness": {"mean": 0.80, "n": 60, "errors": 0}}}


def _offline(tmp_path, e2e_llm):
    """A fake run_eval for e2e (live or mock) and ragas; returns the argv each run got."""
    calls = []

    def fake_main(argv):
        calls.append(argv)
        stage = argv[argv.index("--stage") + 1]
        if "e2e" in argv:
            return manifest(tmp_path / stage, "e2e", stage, {"verdicts": {"all": {"PASS": 45, "FAIL": 45}}, "top1_right": 47,
                                                            "failures": {"retrieval": 30}}, llm=e2e_llm)
        return manifest(tmp_path / stage, "ragas", stage, RAGAS)
    return calls, fake_main


def test_ragas_judges_this_suites_live_e2e_and_the_gate_compares_it(tmp_path):
    """T3-43: ragas gets the e2e folder of the same suite; a mock e2e is never judged (no non-scores in the history)."""
    calls, fake_main = _offline(tmp_path, None)
    s = suite.run(opts(llm="hcx", only=["e2e", "ragas"]), main=fake_main, checks=ALL_OK)
    ragas_argv = calls[1]
    assert ragas_argv[ragas_argv.index("--from-run") + 1] == s["runs"][0]["run"]
    assert s["runs"][0]["metrics"]["실패: retrieval"] == 30 and s["runs"][1]["metrics"] == {"faithfulness": 0.80}
    calls, fake_main = _offline(tmp_path / "mock", "mock")
    s = suite.run(opts(llm="mock", only=["e2e", "ragas"]), main=fake_main, checks=ALL_OK)
    assert len(calls) == 1 and s["runs"][1]["status"] == "skipped" and "mock" in s["runs"][1]["reason"]
    assert s["gate"]["status"] == "skipped"  # never a silent pass


def test_gate_states(tmp_path):
    base = tmp_path / "baseline.json"
    assert suite.gate(None, base)["status"] == "skipped"
    assert suite.gate(RAGAS, base)["status"] == "skipped"  # no baseline file
    base.write_text(json.dumps({"key": KEY, "n": 90, "metrics": RAGAS["gate"], "tolerance": {}}), encoding="utf-8")
    assert suite.gate(RAGAS, base)["status"] == "ok"
    drop = suite.gate({**RAGAS, "gate": {"faithfulness": 0.70, "e2e_pass_rate": 0.5}}, base)
    assert drop["status"] == "drop" and "faithfulness" in drop["reason"]
    other = suite.gate({**RAGAS, "key": {**KEY, "judge": "other"}}, base)
    assert other["status"] == "skipped" and "judge" in other["reason"]
    md = suite.markdown({"stage": "s", "llm": "hcx", "baseline": None, "checks": {}, "runs": [], "gate": drop})
    assert "회귀 게이트" in md and "✕ 하락" in md


def test_markdown_shows_checks_runs_and_deltas():
    s = {"stage": "new", "llm": "mock", "baseline": ["old"], "checks": {"vectors": [False, "응답 없음"]},
         "runs": [{"name": "l1-bm25-record", "what": "BM25", "status": "ok", "reason": "", "run": "/r/20261106-new-l1",
                   "metrics": {"Hit@5": 0.75}, "baseline": "/r/old", "base_metrics": {"Hit@5": 0.7}}]}
    md = suite.markdown(s)
    assert "| vectors | ✕ | 응답 없음 |" in md and "| Hit@5 | 0.700 | 0.750 | +0.050 |" in md and "경로 확인용" in md
    assert "report.html" in suite.html_page(s, md)


def test_e2e_golden_runs_in_the_suite(tmp_path):
    # T3-46: rules (offline label accuracy) and e2e-scenarios (the e2e golden set) are suite runs of their own
    labels = {"fields": {"route": {"acc": 0.9, "n": 10}, "formula": {"acc": 0.6, "n": 5}, "op": {"acc": 1.0, "n": 1}}}
    agent_rates = {k: 0.5 for k in ("conversation_success", "turn_pass", "tool_selection", "state_accuracy", "numeric_accuracy")}
    calls = []

    def fake_main(argv):
        calls.append(argv)
        layer = argv[argv.index("--layer") + 1]
        summary = labels if layer == "rules" else {**agent_rates, "labels": labels}
        return manifest(tmp_path / f"run-{len(calls)}", layer, argv[argv.index("--stage") + 1], summary, llm="hcx")

    s = suite.run(opts(llm="hcx", only=["rules", "e2e-scenarios"]), main=fake_main, checks=ALL_OK)
    runs = {r["name"]: r for r in s["runs"]}
    assert runs["rules"]["metrics"] == {"라벨 route": 0.9, "라벨 formula": 0.6}
    assert runs["e2e-scenarios"]["metrics"]["conversation_success"] == 0.5 and runs["e2e-scenarios"]["metrics"]["라벨 route"] == 0.9
    e2e = calls[1]
    assert "--calc-golden" not in e2e and "--split" in e2e and "--llm" in e2e  # the scenario path, live model
    skipped = suite.run(opts(only=["rules"]), main=fake_main, checks={**ALL_OK, "e2e_golden": (False, "없음")})
    assert skipped["runs"][0]["status"] == "skipped"
