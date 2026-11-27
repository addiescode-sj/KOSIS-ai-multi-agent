"""Ragas layer (plans/T3-19.md): samples are built by code from the golden set and the saved e2e row, low scores point at
a LangGraph node, the layer runs end to end on a fake judge, and the quality gate compares against a baseline.
Offline: no ragas import, no judge call."""

import hashlib
import json

import pytest

from kosis_ai.schemas import Fact, StatAnswer

from evals import quality
from evals.evaluators import ragas_metrics as rm
from evals.schemas import CalcItem

TEXTS = {"101_GOLD": {"text": "통계표: 시도별 출생 조사: 인구동향조사 시도별 경기도 출생건수", "name": "시도별 출생", "survey": "인구동향조사"},
         "101_OTHER": {"text": "통계표: 시도별 사망 조사: 인구동향조사 시도별 경기도 사망건수", "name": "시도별 사망", "survey": "인구동향조사"}}
FACTS = [Fact(id="f1", value=76309, unit="명", period="2025", dims={"행정구역별": "경기도", "항목": "출생건수 (명)"},
              source_ref="DT_GOLD|T10|31|2025")]


def answer(outcome="answered", text="2025년 경기도 출생아 수는 76,309명입니다.\n\n출처: KOSIS / 인구동향조사 / 시도별 출생 (http://x)"):
    if outcome != "answered":
        return StatAnswer.model_validate({"outcome": "refused", "text": "없어요.", "refusal": {"reason": "out_of_scope", "message": "없어요."}})
    return StatAnswer.model_validate({
        "outcome": "answered", "text": text,
        "result": {"facts": [{"id": "f1", "value": 76309.0, "unit": "명", "period": "2025", "dims": {"시도별": "경기도"},
                              "source_ref": "DT_GOLD|T1|31|2025"}]},
        "parts": [{"kind": "text", "text": "2025년 경기도 출생아 수는 "}, {"kind": "fact", "fact_id": "f1"}, {"kind": "text", "text": "입니다."}],
        "source": {"org_id": "101", "tbl_id": "DT_GOLD", "survey_nm": "인구동향조사", "tbl_nm": "시도별 출생"}})


def row(**kw):
    base = {"qid": "q1", "question": "2025년 경기도에서 태어난 아이는 몇 명이에요?", "answer": answer(), "keys": ["101_OTHER", "101_GOLD"],
            "used_table": "101_OTHER", "gold_keys": ["101_GOLD"],
            "search_spans": [{"name": "fuse", "keys": ["101_GOLD", "101_OTHER"]}, {"name": "rerank2", "keys": ["101_OTHER", "101_GOLD"]}],
            "trace_spans": [{"name": "search", "depth": 1, "duration_ms": 30.0, "start_ms": 0.0},
                            {"name": "statistics", "depth": 1, "duration_ms": 9.0, "start_ms": 30.0},
                            {"name": "compose_llm", "depth": 3, "duration_ms": 2.0, "start_ms": 35.0}]}
    return {**base, **kw}


def test_fact_lines_and_reference_are_code_without_numbers_in_the_reference():
    assert rm.fact_lines(answer()) == ["f1: 2025 · 시도별=경기도 = 76,309 명"]
    ref = rm.reference(["101_GOLD"], FACTS, TEXTS)
    assert "인구동향조사의 「시도별 출생」" in ref and "2025" in ref and "출생건수" in ref
    assert "76" not in ref and "(명)" not in ref  # no numbers, no units: the table text may not carry them


def test_samples_skip_what_a_metric_cannot_judge():
    s = rm.samples(row(), FACTS, TEXTS, k=5)
    assert s["faithfulness"]["retrieved_contexts"][0].startswith("f1: 2025")  # the computed facts ground the answer
    assert s["faithfulness"]["retrieved_contexts"][1] == TEXTS["101_OTHER"]["text"]  # + the table it used
    assert "출처" not in s["answer_relevancy"]["response"]  # the source line is checked by code (missing_source)
    assert s["context_precision"]["retrieved_contexts"] == [TEXTS["101_OTHER"]["text"], TEXTS["101_GOLD"]["text"]]  # rank order
    refused = rm.samples(row(answer=answer("refused")), FACTS, TEXTS, k=5)
    assert refused["faithfulness"] is None and refused["answer_relevancy"] is None and refused["context_recall"] is not None
    no_gold = rm.samples(row(), [], TEXTS, k=5)  # not_answerable: no golden facts, no retrieval target
    assert no_gold["context_precision"] is None and no_gold["faithfulness"] is not None


def test_id_scores_are_average_precision_and_hit():
    assert rm.id_scores(["a", "g"], {"g"}, 5) == {"id_context_precision": 0.5, "id_context_recall": 1.0}
    assert rm.id_scores(["g", "a"], {"g"}, 5)["id_context_precision"] == 1.0
    assert rm.id_scores(["a", "b", "g"], {"g"}, 2) == {"id_context_precision": 0.0, "id_context_recall": 0.0}
    assert rm.id_scores(["a"], set(), 5) == {"id_context_precision": None, "id_context_recall": None}


def test_a_run_without_any_table_scores_zero_context_without_a_judge():
    r = row(keys=[], answer=None)
    assert rm.no_contexts(r, FACTS) and not rm.no_contexts(r, [])  # only when there was something to retrieve
    assert rm.samples(r, FACTS, TEXTS, k=5)["context_precision"] is None  # never sent to Ragas (it refuses empty contexts)


def test_low_scores_point_at_graph_nodes():
    r = row()
    assert rm.node_for("context_precision", r, 5) == "search/rerank2"  # rerank2 moved the gold table from 1st to 2nd
    assert rm.node_for("answer_relevancy", r, 5) == "search/rerank2"  # answered from a non-gold table
    assert rm.node_for("faithfulness", r, 5) == "statistics/compose_llm"  # the phase 1 span that wrote the text
    assert rm.node_for("answer_relevancy", row(used_table="101_GOLD"), 5) == "statistics/compose_llm"
    assert rm.node_ms(r, "statistics/compose_llm") == 9.0


def run_layer(tmp_path, monkeypatch, judge):
    """The ragas task over a one-row e2e run folder with a fake judge."""
    from evals.tasks import ragas

    e2e = tmp_path / "e2e"
    e2e.mkdir()
    saved = {**row(), "answer": answer().model_dump(mode="json"), "outcome": "answered", "verdict": "FAIL", "right_table": False,
             "trace_id": "ab" * 16, "span_id": "cd" * 8, "error": None, "keys": ["101_OTHER", "101_GOLD"]}
    (e2e / "results.jsonl").write_text(json.dumps(saved, ensure_ascii=False) + "\n", encoding="utf-8")
    golden = tmp_path / "calc.jsonl"
    golden.write_text(CalcItem(qid="q1", kind="answerable", question=row()["question"], table_key="101_GOLD", table_sha256="x",
                               required_facts=FACTS).model_dump_json() + "\n", encoding="utf-8")
    (e2e / "manifest.json").write_text(json.dumps({
        "run_id": "20261117-000000-e2e", "layer": "e2e", "split": "test", "agent": None,
        "golden": {"sha256": hashlib.sha256(golden.read_bytes()).hexdigest()},
        "source_snapshot": {"tables.jsonl": "t"}, "ops": {"models": ["HCX-DASH-002"]}}), encoding="utf-8")
    monkeypatch.setattr(rm, "table_texts", lambda keys, path: TEXTS)
    monkeypatch.setattr(ragas, "make_scorer", lambda args: (lambda jobs: [judge(m) for _, m, _ in jobs], "0.4.3"))

    class Args:
        from_run, calc_golden, k, split, stage, no_judge_cache = e2e, [golden], 5, "test", "t", False
    return ragas.run(Args)


def test_layer_scores_links_and_summarizes(tmp_path, monkeypatch):
    rows, summary, report = run_layer(tmp_path, monkeypatch, lambda m: (0.2 if m == "faithfulness" else 0.9, None, 5.0))
    [r] = rows
    assert r["faithfulness"] == 0.2 and r["id_context_precision"] == 0.5 and r["llm_calls"] == 4
    assert r["low_nodes"] == {"faithfulness": "statistics/compose_llm"}
    assert r["failure"]["span"] == "statistics/compose_llm" and r["trace_id"] == "ab" * 16
    assert summary["gate"]["faithfulness"] == 0.2 and summary["gate"]["answered"] == 1
    assert summary["key"]["e2e_target"] == "backend.app.main:build_agent+HCX-DASH-002" and summary["key"]["ragas"] == "0.4.3"
    assert "Faithfulness" in report and "statistics/compose_llm" in report
    from evals.tasks import ragas
    assert ragas.span_attributes(r)["kosis.ragas.node.faithfulness"] == "statistics/compose_llm"


def test_a_failed_judgement_is_a_none_score_not_a_failed_run(tmp_path, monkeypatch):
    rows, summary, _ = run_layer(tmp_path, monkeypatch, lambda m: (None, "Timeout", 1.0) if m == "context_recall" else (1.0, None, 1.0))
    assert rows[0]["context_recall"] is None and rows[0]["judge_errors"] == {"context_recall": "Timeout"}
    assert summary["metrics"]["context_recall"] == {"mean": None, "n": 0, "errors": 1}


def test_link_spans_carry_the_scores(tmp_path, monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from evals.runners.agent import link_questions
    from evals.tasks import ragas

    rows, _, _ = run_layer(tmp_path, monkeypatch, lambda m: (0.2, None, 1.0))
    provider, memory = TracerProvider(), InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    link_questions(provider, rows, "ragas", name="eval.ragas", attributes=ragas.span_attributes)
    [span] = memory.get_finished_spans()
    assert span.name == "eval.ragas" and span.links[0].context.trace_id == int("ab" * 16, 16)
    assert span.attributes["kosis.ragas.faithfulness"] == 0.2 and span.attributes["kosis.failure.stage"] == "ragas"


# --- quality gate -----------------------------------------------------------------------------------------------

KEY = {"golden_sha256": "g", "judge": "gpt-4.1-mini"}


def rec(run_id, key=KEY, **metrics):
    return {"run_id": run_id, "n": 90, "key": key, "metrics": {"faithfulness": 0.9, "e2e_pass_rate": 0.4, "answered": 60, **metrics}}


def test_gate_compares_the_newest_record_of_the_same_key_with_the_baseline():
    base = {**rec("1"), "tolerance": {}}
    history = [rec("3", faithfulness=0.5), rec("2", faithfulness=0.89), rec("4", key={**KEY, "judge": "other"}, faithfulness=0.1)]
    newest = quality.latest(history, KEY)
    assert newest["run_id"] == "3"  # largest run id, not the last line; another key (an experiment) never counts
    rows = {r["metric"]: r for r in quality.compare(base, newest)}
    assert not rows["faithfulness"]["ok"] and rows["e2e_pass_rate"]["ok"]
    assert all(r["ok"] for r in quality.compare(base, rec("5", faithfulness=0.875)))  # within the 0.03 judge tolerance


def test_gate_tolerances_count_questions_for_code_metrics():
    base = {**rec("1"), "tolerance": {"faithfulness": 0.1}}
    rows = {r["metric"]: r for r in quality.compare(base, rec("2", faithfulness=0.81, e2e_pass_rate=0.4 - 3 / 90, answered=58))}
    assert rows["faithfulness"]["ok"]  # baseline tolerance wins
    assert not rows["e2e_pass_rate"]["ok"]  # 3 questions > 2/n
    assert rows["answered"]["ok"]  # 2 fewer answered is within tolerance
    assert not quality.compare(base, rec("3", faithfulness=None))[0]["ok"]  # a metric that disappeared fails


def test_record_and_promote_round_trip(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    summary = {"source_run": "e", "n": 90, "key": KEY, "gate": {"faithfulness": 0.9}}
    (run / "manifest.json").write_text(json.dumps({"run_id": "r1", "layer": "ragas", "summary": summary}), encoding="utf-8")
    quality.record("r1", "s", summary, tmp_path / "h.jsonl")
    base = quality.promote(run, {"faithfulness": 0.05}, tmp_path / "b.json")
    assert base["tolerance"] == {"faithfulness": 0.05}
    rec_, rows = quality.check(tmp_path / "h.jsonl", tmp_path / "b.json")
    assert rec_["run_id"] == "r1" and rows[0]["ok"]
    with pytest.raises(SystemExit):
        (run / "manifest.json").write_text(json.dumps({"run_id": "r1", "layer": "e2e", "summary": summary}), encoding="utf-8")
        quality.promote(run, None, tmp_path / "b.json")


def test_a_scenario_e2e_run_is_scored_like_a_calc_golden_run(tmp_path, monkeypatch):
    """T3-63: the e2e golden's scenario run (rows by sid / turn with tool calls) is read into the same row: the last
    summarize_stat answer, every searched candidate, the table it ended on, the calc verdict; the calc file's own hash."""
    from evals.tasks import ragas

    e2e = tmp_path / "e2e"
    e2e.mkdir()
    turn = {"sid": "q1", "turn": 0, "run": 0, "question": row()["question"], "state_key": "101_OTHER",
            "expected_table": "101_GOLD", "calc": {"verdict": "FAIL"}, "pred_labels": {"outcome": "answered"},
            "trace_id": "ab" * 16, "span_id": "cd" * 8, "error": None, "latency_ms": 12.0,
            "calls": [{"name": "search_tables", "args": {}, "result": {"route": "found", "candidates": [
                          {"key": "101_OTHER"}, {"key": "101_GOLD"}]}},
                      {"name": "summarize_stat", "args": {"table_key": "101_OTHER"}, "result": answer().model_dump(mode="json")}]}
    (e2e / "results.jsonl").write_text(json.dumps(turn, ensure_ascii=False) + "\n", encoding="utf-8")
    golden = tmp_path / "e2e_golden_calc.jsonl"
    golden.write_text(CalcItem(qid="q1", kind="answerable", question=row()["question"], table_key="101_GOLD", table_sha256="x",
                               required_facts=FACTS).model_dump_json() + "\n", encoding="utf-8")
    (e2e / "manifest.json").write_text(json.dumps({
        "run_id": "20261127-000000-e2e", "layer": "e2e", "split": "test", "agent": None,
        "golden": {"sha256": "scenarios+calc", "files": {"e2e_golden.jsonl": "s",
                                                         "e2e_golden_calc.jsonl": hashlib.sha256(golden.read_bytes()).hexdigest()}},
        "source_snapshot": {"tables.jsonl": "t"}, "ops": {"models": ["HCX-DASH-002"]}}), encoding="utf-8")
    monkeypatch.setattr(rm, "table_texts", lambda keys, path: TEXTS)
    monkeypatch.setattr(ragas, "make_scorer", lambda args: (lambda jobs: [(0.9, None, 5.0) for _ in jobs], "0.4.3"))

    class Args:
        from_run, calc_golden, k, split, stage, no_judge_cache = e2e, [golden], 5, "test", "t", False
    [r], summary, _ = ragas.run(Args)
    assert r["qid"] == "q1" and r["verdict"] == "FAIL" and r["faithfulness"] == 0.9
    assert r["id_context_precision"] == 0.5 and r["trace_id"] == "ab" * 16  # gold second among the searched candidates
    assert summary["gate"]["answered"] == 1 and summary["gate"]["e2e_right_table_rate"] == 0.0
