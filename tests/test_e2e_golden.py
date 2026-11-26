"""T3-46 e2e golden: label derivation and grading, builder helpers, the e2e scenario path (oracle self-check)."""

import json
import signal

import pytest
from kosis_ai.schemas import Fact

from evals.builders.e2e_golden import formula_labels, ratio_question
from evals.evaluators import labels as lab
from evals.schemas import CalcItem, TurnLabels


def test_formula_of_reads_phase1_derivations():
    assert lab.formula_of("f2 - f1", "명") == "diff"
    assert lab.formula_of("f2 - f1", "%p") == "pp_diff"  # a difference of percentages, whichever fn wrote it
    assert lab.formula_of("(f2 / f1 - 1) * 100", "%") == "pct_change"
    assert lab.formula_of("f1 / f2", "배") == "ratio"
    assert lab.formula_of("((f2 / f1) ** (1 / 9) - 1) * 100", "%") == "cagr"
    assert lab.formula_of("f1 / f2 * 1000", None) == "per"
    assert lab.formulas([{"derivation": "f3 - f1", "unit": "명"}, {"derivation": None}, Fact(id="f4", value=1.0,
                         derivation="(f3 / f1 - 1) * 100", unit="%", round=1)]) == ["diff", "pct_change"]


def test_reading_and_route():
    kw = dict(resolved="q", question="q", by="rule")
    assert lab.reading_of(had_table=False, use_previous_table=False, chart_only=False, **kw) == "new"
    assert lab.reading_of(had_table=True, use_previous_table=True, chart_only=False, **kw) == "reference"
    assert lab.reading_of(had_table=True, use_previous_table=True, chart_only=False, resolved="2024년 q", question="작년은?",
                          by="rule") == "carry_over"
    assert lab.reading_of(had_table=True, use_previous_table=False, chart_only=False, resolved="q", question="q", by="none") == "defer"
    assert lab.route_of(TurnLabels(reading="carry_over", outcome="answered")) == "rewrite"
    assert lab.route_of(TurnLabels(reading="new", outcome="clarify")) == "clarify"
    assert lab.route_of(TurnLabels(reading="new", sub_questions=["a", "b"], outcome="answered")) == "rewrite"
    assert lab.route_of(TurnLabels(reading="new", outcome="answered")) == "rag"


def test_grade_only_gold_fields_formula_subset_and_forbidden():
    gold = TurnLabels(failure_type="formula", outcome="answered", qtype="trend", formula=["pp_diff"],
                      formula_forbidden=["pct_change"], refusal_reasons=None)
    assert lab.grade(gold, TurnLabels(outcome="answered", qtype="trend", formula=["pp_diff"])) == \
        {"outcome": True, "qtype": True, "formula": True}
    assert lab.grade(gold, TurnLabels(outcome="answered", qtype="trend", formula=["pp_diff", "pct_change"]))["formula"] is False
    refuse = TurnLabels(outcome="refused", refusal_reasons=["no_item", "out_of_scope"], sub_questions=[])
    assert lab.grade(refuse, TurnLabels(outcome="refused", refusal_reasons=["out_of_scope"], sub_questions=["x"])) == \
        {"outcome": True, "refusal_reasons": True, "sub_questions": True}  # one part = not split
    assert lab.grade(gold, None)["outcome"] is False
    s = lab.summarize([{"gold_labels": {"failure_type": "formula", "qtype": "trend"}, "pred_labels": {"qtype": "value"},
                        "label_hits": {"qtype": False}},
                       {"gold_labels": {"failure_type": "formula", "qtype": "trend"}, "pred_labels": {"qtype": "trend"},
                        "label_hits": {"qtype": True}}])
    assert s["fields"]["qtype"] == {"acc": 0.5, "n": 2} and s["confusion"]["qtype"] == [("trend → value", 1)]


def _item(question, unit="명", dims=({"시군구별": "동구"}, {"시군구별": "서구"}), type_="compare"):
    facts = [Fact(id="f1", value=10.0, unit=unit, period="2025", dims=dims[0], source_ref="T|I|A|2025"),
             Fact(id="f2", value=5.0, unit=unit, period="2025", dims=dims[1], source_ref="T|I|B|2025")]
    return CalcItem(qid="q", kind="answerable", type=type_, question=question, table_key="101_DT_TEST", table_sha256="s",
                    required_facts=facts)


def test_ratio_question_and_formula_labels():
    assert ratio_question(_item("2025년 혼인 건수, 동구랑 서구 차이는?")) == "2025년 혼인 건수, 동구는 서구의 몇 배야?"
    assert ratio_question(_item("2025년 동구랑 서구의 혼인 건수 차이는?")) is None  # 지표 낱말이 뒤에 오면 만들지 않음
    assert formula_labels(_item("x", unit="%", type_="trend")) == {"formula": ["pp_diff"], "formula_forbidden": ["pct_change"]}
    assert formula_labels(_item("x", type_="trend")) == {"formula": ["pct_change"]}
    assert formula_labels(_item("x", unit="%")) == {"formula": ["pp_diff"]}


def test_rule_labels_carry_the_conversation_on_the_gold_table(monkeypatch):
    from kosis_rag.parse.node import parse_query

    from evals.schemas import ScenarioTurn

    seen = []
    monkeypatch.setattr(lab, "phase1_rules", lambda q, key, res: seen.append((q, key)) or {"outcome": "answered", "qtype": "value"})
    turns = [ScenarioTurn.model_validate({"question": "2025년 서울 혼인 건수는?", "expect": {"table_key": "T"}}),
             ScenarioTurn.model_validate({"question": "부산은?", "expect": {"table_key": "T"}})]
    preds = lab.rule_labels(turns, lambda q: parse_query(q), resources_of=None)
    assert [p.reading for p in preds] == ["new", "carry_over"]
    assert preds[1].route == "rewrite" and "부산" in preds[1].resolved_question
    assert seen[1] == (preds[1].resolved_question, "T")  # phase 1 is asked the rewritten question on the gold table


def test_e2e_scenario_path_oracle_self_check(monkeypatch, tmp_path):
    from evals import run_eval

    item = _item("2025년 혼인 건수, 동구랑 서구 차이는?")
    calc = tmp_path / "calc.jsonl"
    calc.write_text(item.model_dump_json() + "\n", encoding="utf-8")
    scen = tmp_path / "e2e.jsonl"
    labels = TurnLabels(failure_type="numbers", route="rag", reading="new", outcome="answered", qtype="compare",
                        formula=["diff"]).model_dump(exclude_none=True)
    scen.write_text(json.dumps({"sid": "E2E-N-compare-001", "split": "test", "turns": [{"question": item.question, "expect": {
        "tools": [{"name": "search_tables"}, {"name": "summarize_stat", "args": {"table_key": "101_DT_TEST"}}], "table_key": "101_DT_TEST",
        "calc_qid": "q", "labels": labels}}]}, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(run_eval, "E2E_SCENARIOS", scen)
    monkeypatch.setattr(run_eval, "E2E_CALC", calc)
    monkeypatch.setattr(run_eval, "REPORTS", tmp_path / "reports")
    from evals import ops, report_html
    from evals.evaluators import calculation

    history: list = []
    monkeypatch.setattr(ops, "append_history", lambda *a, **kw: history.append(a))
    monkeypatch.setattr(report_html, "read_history", lambda: [])
    monkeypatch.setattr(calculation, "read_cells", lambda path, refs=None: {})
    out = run_eval.main(["--layer", "e2e", "--oracle", "--stage", "t", "--split", "all"])
    assert history == []  # an oracle run calls nothing: no ops_history line
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert {f: v["acc"] for f, v in m["summary"]["labels"]["fields"].items()} == \
        {"route": 1.0, "reading": 1.0, "outcome": 1.0, "qtype": 1.0, "formula": 1.0}
    assert "분류 라벨" in (out / "report.md").read_text(encoding="utf-8") and (out / "report.html").exists()


def test_rules_layer_leaves_search_gate_turns_to_live_e2e():
    # an out-of-scope refusal has no gold table: phase 1 rules never see it offline, so its outcome is not graded there
    from evals.schemas import AgentScenario
    from evals.tasks.rules import rows_of

    gold = TurnLabels(failure_type="classify", route="refuse", reading="new", outcome="refused",
                      refusal_reasons=["out_of_scope"]).model_dump(exclude_none=True)
    s = AgentScenario.model_validate({"sid": "E2E-C-refuse-001", "turns": [{"question": "월평균 가구소득은?", "expect": {
        "tools": [{"name": "search_tables"}], "refuse": True, "labels": gold,
        "rule_pred": {"reading": "new", "route": "rag"}}}]})
    (row,) = rows_of([s], parse=None)
    assert row["search_gate"] and row["label_hits"] == {"reading": True}


def test_formula_grade_reads_plan_steps_as_compute_would():
    gold = TurnLabels(formula=["pp_diff"], formula_forbidden=["pct_change"])
    # the plan diffs two percentages (= %p) and holds an optional pct_change that compute drops on a % series
    assert lab.grade(gold, TurnLabels(formula=["diff", "pct_change"], formula_optional=["pct_change"]))["formula"] is True
    assert lab.grade(gold, TurnLabels(formula=["diff", "pct_change"]))["formula"] is False  # a required pct_change is wrong
    s = lab.summarize([{"gold_labels": {"formula": ["cagr"]}, "pred_labels": {"plan_unresolved": True},
                        "label_hits": {"formula": False}}])
    assert s["confusion"]["formula"] == [("['cagr'] → 규칙 미결정", 1)]


# with_rule_pred times a scenario out with SIGALRM, which Windows does not have (the timeout is off there)
@pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="no SIGALRM on Windows: with_rule_pred has no timeout there")
def test_rule_pred_guards_large_tables_timeouts_and_resumes(tmp_path):
    # T3-46: a build was force-quit while phase 1 loaded multi-million-row tables -> skip, time out, resume
    import time

    from evals.builders import e2e_golden as b
    from evals.evaluators.labels import TableTooLarge, table_resources
    from evals.schemas import AgentScenario

    def scen(sid, key):
        return AgentScenario.model_validate({"sid": sid, "turns": [{"question": "2025년 혼인 건수는?", "expect": {"table_key": key}}]})

    def resources_of(key):
        if key == "BIG":
            raise TableTooLarge("BIG: 9,000,000 rows > 2,000,000")
        if key == "SLOW":
            time.sleep(3)
        raise LookupError("no phase 1 in this test")

    cache = tmp_path / "cache.jsonl"
    rows = [scen("A", "BIG"), scen("B", "SLOW"), scen("C", "OK")]
    out = b.with_rule_pred(rows, timeout=1, cache_path=cache, resources_of=resources_of, parse=lambda q: __import__(
        "kosis_rag.parse.node", fromlist=["parse_query"]).parse_query(q))
    rules = [s.turns[0].expect.rule_pred.judge_rule for s in out]
    assert rules[0].startswith("skipped: large table") and rules[1] == "error: timeout 1s" and rules[2] == "error: LookupError"
    assert len(cache.read_text(encoding="utf-8").splitlines()) == 3  # written as each scenario finished
    again = b.with_rule_pred(rows, timeout=1, cache_path=cache, resources_of=lambda k: 1 / 0, parse=None or (lambda q: None))
    assert [s.turns[0].expect.rule_pred.judge_rule for s in again] == rules  # resumed from the cache, nothing recomputed
    # the row cap is checked before any file is opened
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "tables.csv").write_text("key,rows\nBIG,9000000\n", encoding="utf-8")
    import pytest

    with pytest.raises(TableTooLarge):
        table_resources(tmp_path)("BIG")
