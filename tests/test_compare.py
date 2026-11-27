"""plans/T3-63.md: paired before -> after verdict (evals/compare.py) on made-up run folders."""

import json

import pytest

from evals import compare


def make_run(tmp_path, name, passed, *, golden="g1", p95=1000.0, cost=0.3, violation=(), latency=1000.0):
    """A run folder with one single-turn question per entry of `passed` (sids Q0, Q1, ...)."""
    d = tmp_path / name
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"run_id": name, "layer": "e2e", "split": "test",
                                                 "golden": {"sha256": golden}, "git": "abc1234"}))
    (d / "ops.json").write_text(json.dumps({"latency_ms": {"p95": p95}, "cost": {"per_request_mean": cost}}))
    rows = [{"sid": f"Q{i}", "turn": 0, "run": 0, "passed": ok, "question": f"q{i}", "latency_ms": latency,
             "unsupported_numbers": [1] if i in violation else [], "missing_sources": [], "errors": []}
            for i, ok in enumerate(passed)]
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return compare.load_run(d)


def judge(base, after, sources=None):
    c = compare.paired(base, after, sources or {})
    return compare.verdict(c), c


def test_the_bootstrap_interval_is_fixed_by_its_seed_and_brackets_the_mean():
    diff = [1] * 10 + [0] * 100 + [-1] * 2
    lo, hi = compare.boot(diff)
    assert (lo, hi) == compare.boot(diff)
    assert lo < sum(diff) / len(diff) < hi and lo > 0  # 10 wins, 2 losses of 112: a gain the interval keeps above 0


def test_a_clear_gain_at_the_same_cost_is_adopted(tmp_path):
    base = make_run(tmp_path, "b", [False] * 12 + [True] * 100)
    after = make_run(tmp_path, "a", [True] * 12 + [True] * 100)
    (decision, why), c = judge(base, after)
    assert decision == "채택" and len(c["wins"]) == 12 and not c["losses"] and why[0].startswith("정확도 개선")


def test_wins_and_losses_that_cancel_are_no_gain_and_a_costlier_change_is_rejected(tmp_path):
    base = make_run(tmp_path, "b", [True] * 3 + [False] * 3 + [True] * 50)
    after = make_run(tmp_path, "a", [False] * 3 + [True] * 3 + [True] * 50, p95=1500.0)
    (decision, why), c = judge(base, after)
    assert c["diff"] == 0 and len(c["wins"]) == 3 and len(c["losses"]) == 3
    assert decision == "기각" and "차이 미확인" in why[0]


def test_no_accuracy_difference_but_cheaper_and_faster_is_adopted(tmp_path):
    base = make_run(tmp_path, "b", [True] * 50, p95=2000.0, cost=0.4)
    after = make_run(tmp_path, "a", [True] * 50, p95=1500.0, cost=0.3)
    assert judge(base, after)[0][0] == "채택"


def test_new_violations_over_the_tolerance_reject_whatever_the_accuracy(tmp_path):
    base = make_run(tmp_path, "b", [False] * 12 + [True] * 100)
    after = make_run(tmp_path, "a", [True] * 112, violation=(20, 21, 22))
    (decision, why), _ = judge(base, after)
    assert decision == "기각" and why[0].startswith("가드레일")


def test_a_gain_bought_with_one_source_is_held(tmp_path):
    base = make_run(tmp_path, "b", [False] * 15 + [True] * 3 + [True] * 100)
    after = make_run(tmp_path, "a", [True] * 15 + [False] * 3 + [True] * 100)
    sources = {f"Q{i}": ("share" if 15 <= i < 18 else "trend") for i in range(118)}
    (decision, why), _ = judge(base, after, sources)
    assert decision == "보류" and "share" in why[-1]


def test_runs_of_another_golden_are_refused(tmp_path):
    base = make_run(tmp_path, "b", [True] * 5)
    other = make_run(tmp_path, "a", [True] * 5, golden="g2")
    with pytest.raises(compare.Incomparable, match="golden"):
        compare.check_comparable(base, other)


def test_the_aa_check_flags_a_session_whose_latency_drifts(tmp_path):
    base = make_run(tmp_path, "b", [True] * 20, p95=1000.0)
    steady = make_run(tmp_path, "s", [True] * 20, p95=1100.0)
    drifting = make_run(tmp_path, "d", [True] * 20, p95=1500.0)
    assert compare.aa_check(base, steady)["ok"] and not compare.aa_check(base, drifting)["ok"]


def test_the_cli_writes_the_report_and_refuses_with_exit_2(tmp_path, capsys):
    make_run(tmp_path, "b", [False] * 12 + [True] * 100)
    make_run(tmp_path, "a", [True] * 112)
    make_run(tmp_path, "x", [True] * 112, golden="g2")
    out = tmp_path / "cmp"
    assert compare.main(["--base", str(tmp_path / "b"), "--after", str(tmp_path / "a"), "--out", str(out),
                         "--scenarios", str(tmp_path / "none.jsonl")]) == 0
    assert "판정: **채택**" in (out / "compare.md").read_text() and json.loads((out / "compare.json").read_text())["decision"] == "채택"
    assert compare.main(["--base", str(tmp_path / "b"), "--after", str(tmp_path / "x"), "--out", str(out)]) == 2


def layer_run(tmp_path, name, layer, rows):
    d = tmp_path / name
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"run_id": name, "layer": layer, "split": "test", "golden": {"sha256": "g"}}))
    (d / "ops.json").write_text(json.dumps({"latency_ms": {"p95": 100.0}, "cost": {"per_request_mean": 0.1}}))
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return compare.load_run(d)


def test_each_layer_has_its_own_question_pass():
    """l1 the right table first (or a right refusal), l2 every slot, l3 the PASS verdict, agent / e2e the turn's pass."""
    assert compare.passed("l1", {"answerable": True, "hit@1": 1}) and not compare.passed("l1", {"answerable": True, "hit@1": 0})
    assert compare.passed("l1", {"answerable": False, "no_answer_pred": True})
    assert compare.passed("l2", {"all_fields": 1}) and not compare.passed("l2", {"all_fields": 0})
    assert compare.passed("l3", {"verdict": "PASS"}) and not compare.passed("l3", {"verdict": "PARTIAL"})
    assert compare.passed("e2e", {"passed": True}) and not compare.passed("agent", {"passed": False})


def test_l3_runs_pair_by_qid_and_group_by_kind(tmp_path):
    base = layer_run(tmp_path, "b", "l3", [{"qid": f"Q{i}", "kind": "answerable", "verdict": "FAIL", "latency_ms": 10}
                                           for i in range(12)] + [{"qid": "R", "kind": "default_allowed", "verdict": "PASS"}])
    after = layer_run(tmp_path, "a", "l3", [{"qid": f"Q{i}", "kind": "answerable", "verdict": "PASS", "latency_ms": 10}
                                            for i in range(12)] + [{"qid": "R", "kind": "default_allowed", "verdict": "PASS"}])
    c = compare.paired(base, after, {})
    assert (c["base_pass"], len(c["wins"]), c["by_source"]["answerable"]["wins"]) == (1 / 13, 12, 12)
    assert compare.verdict(c)[0] == "채택"
