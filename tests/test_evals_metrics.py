"""L1 and L3 evaluators (T2-03): ranked-key metrics, the no-answer threshold, and fact grading rules."""

from types import SimpleNamespace

from kosis_ai.schemas import Fact, Refusal, Source, StatAnswer, StatResult, TextPart

from evals.evaluators import calculation, retrieval
from evals.schemas import CalcItem, RetrievalItem

BASE = {"qid": "q1", "split": "test", "source": "scenario", "question": "q"}


def test_hit_mrr_and_complete_over_slots():
    item = RetrievalItem(**BASE, qtype="multi_table", required=[["a", "a2"], ["b"]])
    row = retrieval.score_item(item, ["x", "a2", "y", "b"], judged={"x"})
    assert row["rank"] == 2 and row["rr"] == 0.5
    assert row["hit@1"] == 0 and row["hit@3"] == 1
    assert row["complete@2"] == 0 and row["slot_recall@2"] == 0.5  # a2 is 2nd, b is 4th
    assert row["complete@3"] == 0 and row["slot_recall@3"] == 0.5  # b is 4th
    assert row["complete@5"] == 1
    assert row["unjudged@10"] == 0.25  # y: neither gold nor judged

    assert retrieval.score_item(item, ["b", "a"])["complete@2"] == 1  # both slots in the top 2


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


SOURCE = Source(org_id="101", tbl_id="DT_X", survey_nm="인구동향조사", tbl_nm="출생아수")


def answered(facts: list[Fact], text: str = "", source: Source | None = SOURCE) -> StatAnswer:
    """Phase 1's answer: parts, and the rendered text ending with the source line (render/text.py final_text)."""
    parts = [TextPart(text=text)] if text else []
    full = f"{text}\n\n출처: {source.citation()}" if source else text
    return StatAnswer.model_construct(outcome="answered", result=StatResult(facts=facts), parts=parts, text=full,
                                      refusal=None, clarify_options=[], default_applied={}, source=source)


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


def test_an_answer_without_its_source_is_partial():
    """T3-40 (KOS-196): every answer cites KOSIS / survey / table; refusals are not checked."""
    want = [Fact(id="f1", value=100.0, source_ref=REF)]
    item = calc(kind="answerable", required_facts=want)
    assert calculation.grade(item, answered(want), CELLS).verdict == "PASS"
    no_source = calculation.grade(item, answered(want, source=None), CELLS)
    assert (no_source.verdict, no_source.errors) == ("PARTIAL", ["missing_source"])
    uncited = answered(want).model_copy(update={"text": "2024년 100명"})
    assert calculation.grade(item, uncited, CELLS).errors == ["missing_source"]
    assert calculation.grade(item, uncited, CELLS, cite=False).verdict == "PASS"  # the agent layer checks its message
    refused = StatAnswer.model_construct(outcome="refused", result=StatResult(), parts=[], clarify_options=[], text="x",
                                         default_applied={}, refusal=Refusal(reason="no_period", message="x"), source=None)
    assert "missing_source" not in calculation.grade(calc(kind="not_answerable", allowed_reasons=["no_period"]), refused, CELLS).errors


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


def test_read_cells_streams_only_the_wanted_refs(tmp_path):
    """T3-39: a large table is scanned batch by batch and only the graded cells become Python objects."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = [{"org_id": "101", "tbl_id": "DT_X", "source": "api", "prd_se": "Y", "period": str(2000 + i), "item_id": "T1",
             "item_name": "출생아수", "unit": "명", "dim1_name": "행정구역별", "dim1_code": "11", "dim1_label": "서울",
             "dim2_name": None, "dim2_code": None, "dim2_label": None, "value_raw": str(i), "value": float(i), "value_kind": "ok"}
            for i in range(10)]
    path = tmp_path / "101_DT_X.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    full = calculation.read_cells(path)
    assert len(full) == 10 and full["DT_X|T1|11|2003"].value == 3.0
    part = calculation.read_cells(path, frozenset({"DT_X|T1|11|2003", "DT_X|T1|11|2007", "DT_X|T1|11|2099"}))
    assert set(part) == {"DT_X|T1|11|2003", "DT_X|T1|11|2007"} and part["DT_X|T1|11|2007"] == full["DT_X|T1|11|2007"]
    assert calculation.read_cells(path, frozenset()) == {}
    item = calc(kind="answerable", required_facts=[Fact(id="f1", value=3.0, source_ref="DT_X|T1|11|2003", period="2003")])
    answer = StatAnswer.model_construct(outcome="answered", result=StatResult(facts=[
        Fact(id="f1", value=3.0, source_ref="DT_X|T1|11|2003", period="2003"),
        Fact(id="f2", value=5.0, source_ref="DT_X|T1|11|2005", period="2005")]), parts=[])
    assert calculation.grade_refs(item, answer) == {"DT_X|T1|11|2003", "DT_X|T1|11|2005"}
    assert calculation.grade(item, answer, calculation.read_cells(path, calculation.grade_refs(item, answer)),
                             cite=False).verdict == "PASS"  # cells only; the source check is tested apart (T3-40)


def test_ops_cost_is_priced_per_model():
    """T3-39: a turn that calls two models (follow-up reader + phase 1) is priced per model; a mixed currency or an
    unpriced model gives no total, and an old row with models joined by ',' cannot be split."""
    from evals import ops

    pricing = {"a": {"input_per_1k": 1.0, "output_per_1k": 2.0, "currency": "KRW"},
               "b": {"input_per_1k": 10.0, "output_per_1k": 20.0, "currency": "KRW"},
               "usd": {"input_per_1k": 1.0, "output_per_1k": 1.0, "currency": "USD"}}
    row = {"latency_ms": 1, "input_tokens": 3000, "output_tokens": 1000, "model": "a,b", "failed": False, "retried": False,
           "tokens_by_model": {"a": {"input": 1000, "output": 500}, "b": {"input": 2000, "output": 500}}}
    op = ops.summarize([row], pricing=pricing)
    assert op["cost"]["total"] == 1.0 + 1.0 + 20.0 + 10.0 and op["cost"]["currency"] == "KRW" and op["models"] == ["a", "b"]
    assert op["cost"]["by_model"]["b"] == {"input": 2000, "output": 500, "cost": 30.0}
    old = {**row, "tokens_by_model": {}}  # written before T3-39: totals only, two models joined
    assert ops.summarize([old], pricing=pricing)["cost"]["total"] is None
    mixed = {**row, "tokens_by_model": {"a": {"input": 1000, "output": 0}, "usd": {"input": 1000, "output": 0}}}
    assert ops.summarize([mixed], pricing=pricing)["cost"]["total"] is None
    unpriced = {**row, "tokens_by_model": {"a": {"input": 1000, "output": 0}, "zzz": {"input": 1, "output": 0}}}
    assert ops.summarize([unpriced], pricing=pricing)["cost"]["total"] is None
    assert ops.summarize([unpriced], pricing=pricing)["cost"]["unpriced_models"] == ["zzz"]
