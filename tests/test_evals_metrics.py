"""L1 and L3 evaluators (T2-03): ranked-key metrics, the no-answer threshold, and fact grading rules."""

from types import SimpleNamespace

from kosis_ai.schemas import Fact, Refusal, StatAnswer, StatResult, TextPart

from evals.evaluators import calculation, retrieval
from evals.schemas import CalcItem, RetrievalItem

BASE = {"qid": "q1", "split": "test", "source": "scenario", "question": "q"}


def test_hit_mrr_and_complete_over_slots():
    item = RetrievalItem(**BASE, qtype="multi_table", required=[["a", "a2"], ["b"]])
    row = retrieval.score_item(item, ["x", "a2", "y", "b"], judged={"x"})
    assert row["rank"] == 2 and row["rr"] == 0.5
    assert row["hit@1"] == 0 and row["hit@3"] == 1
    assert row["complete@3"] == 0 and row["slot_recall@3"] == 0.5  # b is 4th
    assert row["complete@5"] == 1
    assert row["unjudged@10"] == 0.25  # y: neither gold nor judged


def test_no_answer_threshold_and_prf():
    rows = [(0.9, False), (0.8, False), (0.2, True), (None, True), (0.85, True)]
    tau = retrieval.best_tau(rows)
    # tau 0.8 refuses 0.2 and None (F1 0.8); tau 0.9 also refuses 0.85 and wrongly 0.8 (F1 0.857), so 0.9 wins
    assert tau == 0.9
    m = retrieval.prf([(retrieval.predict_no_answer(s, tau), g) for s, g in rows])
    assert (m["tp"], m["fp"], m["fn"]) == (3, 1, 0)


REF = "DT_X|T1|11|2024"
CELLS = {REF: SimpleNamespace(value=100.0), "DT_X|T1|11|2023": SimpleNamespace(value=80.0)}


def calc(**kw) -> CalcItem:
    return CalcItem(qid="c1", question="q", table_key="101_DT_X", table_sha256="0", **kw)


def answered(facts: list[Fact], text: str = "") -> StatAnswer:
    parts = [TextPart(text=text)] if text else []
    return StatAnswer.model_construct(outcome="answered", result=StatResult(facts=facts), parts=parts,
                                      refusal=None, clarify_options=[], default_applied={})


def test_cell_and_derived_facts():
    want = [Fact(id="f1", value=100.0, source_ref=REF, unit="명"),
            Fact(id="f0", value=80.0, source_ref="DT_X|T1|11|2023"),
            Fact(id="g", value=25.0, derivation="(f1 / f0 - 1) * 100", round=1)]
    item = calc(kind="answerable", required_facts=want)
    assert calculation.grade(item, answered(want), CELLS).verdict == "PASS"
    wrong_year = [Fact(id="f1", value=80.0, source_ref="DT_X|T1|11|2023", unit="명")]
    g = calculation.grade(calc(kind="answerable", required_facts=want[:1]), answered(wrong_year), CELLS)
    assert g.verdict == "FAIL" and g.errors == ["wrong_period"]


def test_unsupported_text_number_fails():
    want = [Fact(id="f1", value=100.0, source_ref=REF)]
    g = calculation.grade(calc(kind="answerable", required_facts=want), answered(want, "2024년 약 120명"), CELLS)
    assert "unsupported_claim" in g.errors and g.verdict == "FAIL"


def test_refusal_reason_and_run_failure():
    item = calc(kind="not_answerable", allowed_reasons=["no_period"])
    refused = StatAnswer.model_construct(outcome="refused", result=StatResult(), parts=[], clarify_options=[],
                                         default_applied={}, refusal=Refusal(reason="no_item", message="x"))
    assert calculation.grade(item, refused, CELLS).errors == ["wrong_reason"]
    failed = calculation.grade(item, None, CELLS, "HTTP 429")
    assert failed.verdict == "FAIL" and failed.errors == ["run_failure"]


def test_ops_tokens_cost_and_failure_rate():
    from evals import ops

    assert ops.tokens_of({"promptTokens": 10, "completionTokens": 3}) == (10, 3)
    assert ops.tokens_of({"input_tokens": 7, "output_tokens": 2}) == (7, 2)
    rows = [{"latency_ms": 10, "input_tokens": 1000, "output_tokens": 500, "model": "m", "failed": False, "retried": True},
            {"latency_ms": 30, "input_tokens": 0, "output_tokens": 0, "model": "m", "failed": True, "retried": False}]
    unpriced = ops.summarize(rows, pricing={})
    assert unpriced["cost"]["total"] is None and unpriced["cost"]["unpriced_models"] == ["m"]  # never invented
    priced = ops.summarize(rows, pricing={"m": {"input_per_1k": 2.0, "output_per_1k": 4.0, "currency": "KRW"}})
    assert priced["cost"]["total"] == 4.0  # 1.0K * 2 + 0.5K * 4
    assert priced["tool_failure_rate"] == 0.5 and priced["retry_rate"] == 0.5
    assert priced["tokens"] == {"input": 1000, "output": 500, "per_call_mean": 750.0}
