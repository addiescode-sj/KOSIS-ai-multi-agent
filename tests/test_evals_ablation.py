"""T2-28: L2 slot grading, filter recall, slot ablation and L0 fill rates (no model, no server; made-up tables)."""

import pytest
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from kosis_ai.schemas import TableInfo
from kosis_rag.filters import TableFilter
from kosis_rag.query import ConditionRetriever, parse_query
from kosis_rag.schemas import TableClass, TableRecord

from evals.evaluators import metadata, query, retrieval
from evals.runners import ablation
from evals.schemas import QueryItem, RetrievalItem

offline = pytest.mark.offline
BASE = {"qid": "q1", "split": "dev", "source": "scenario"}


def _record(key, name, periods, classes):
    info = TableInfo(
        key=key, org_id="101", tbl_id=key.split("_", 1)[1], survey_name="인구동향조사", organization="국가데이터처",
        table_name=name, period="년", source="api", coverage="A_정상", rows=1, numeric_rows=1, symbol_rows=0,
        parquet=f"values/{key}.parquet", url=f"http://kosis.kr/{key}", menu_path="인구",
    )
    return TableRecord(info=info, cycle=["Y"], prd_se=["Y"], periods=periods, start=int(periods[0][:4]),
                       end=int(periods[-1][:4]), classes=[TableClass(name=n, labels=l, n_labels=len(l)) for n, l in classes])


SIDO = ["전국", "서울특별시", "부산광역시", "대구광역시"]
RECORDS = {
    "101_DT_MAR": _record("101_DT_MAR", "시도/성별 혼인건수", ["2020", "2023"], [("행정구역별", SIDO), ("성별", ["계"])]),
    "101_DT_BIRTH": _record("101_DT_BIRTH", "시도별 출생아수", ["2023"], [("행정구역별", SIDO)]),
    "101_DT_OLD": _record("101_DT_OLD", "혼인 통계", ["2010"], []),  # says no measure, no admin class
}


@offline
def test_l2_oracle_is_one_and_invented_slots_are_wrong():
    item = QueryItem(qid="q1", split="dev", question="q", period_start="2023", period_end="2023",
                     regions=["서울특별시"], years=[2023], dimensions=[], measures=["출생"])
    oracle = {"period_start": "2023", "period_end": "2023", "cycle": None, "regions": ["서울특별시"], "years": [2023],
              "dimensions": [], "measures": ["출생", "출산", "생아"]}  # the parser's search words hold the concept
    assert {v for k, v in query.score_query(item, oracle).items() if k != "pr"} == {1}
    row = query.score_query(item, {**oracle, "dimensions": ["성"]})  # a breakdown the question never named
    assert row["dimensions"] == 0 and row["all_fields"] == 0
    draft = QueryItem(qid="q2", split="dev", question="q", cycle="M")  # T2-14 draft: no years, dimensions, measures
    assert set(query.score_query(draft, oracle)) == {"period_start", "period_end", "cycle", "regions", "all_fields", "pr"}


@offline
def test_filter_recall_needs_every_table_slot():
    item = RetrievalItem(**BASE, qtype="multi_table", question="q", required=[["a", "a2"], ["b"]])
    assert retrieval.filter_pass(item, {"a2", "b", "x"})
    assert not retrieval.filter_pass(item, {"a", "a2"})  # slot b removed


@offline
def test_combos_and_masking_follow_to_filter():
    assert len(ablation.combos()) == 12 and len(ablation.combos(["year", "cycle", "region"])) == 8
    parsed = parse_query("2005년과 2025년 서울 성별 혼인 건수")
    f = ablation.filter_for(parsed, frozenset({"year"}))
    assert f.years == [2005, 2025] and f.mode == "partial"  # same strict/partial rule as ConditionRetriever
    assert not (f.regions or f.dimensions or f.measures)
    assert ablation.filter_for(parsed, frozenset()).is_empty()
    # a gold label without `years`: one year stays a year, an open range becomes the same span the parser sends
    one = QueryItem(qid="q", split="dev", question="q", period_start="2023", period_end="2023")
    assert ablation.filter_for(ablation.gold_parsed(one), frozenset({"year"})).years == [2023]
    open_from = QueryItem(qid="q", split="dev", question="1995년부터 5년마다 총인구", period_start="1995", cycle="F")
    gold_f = ablation.filter_for(ablation.gold_parsed(open_from), frozenset({"year", "cycle"}))
    assert gold_f == ablation.filter_for(parse_query(open_from.question), frozenset({"year", "cycle"}))
    assert (gold_f.years, gold_f.period_from, gold_f.period_to) == ([], 1995, None)
    # a gold measure concept filters with the parser's words for it; a concept the parser does not know stays as is
    births = QueryItem(qid="q", split="dev", question="출생아 수", measures=["출생"])
    assert (ablation.filter_for(ablation.gold_parsed(births), frozenset({"measure"}))
            == ablation.filter_for(parse_query(births.question), frozenset({"measure"})))
    homes = QueryItem(qid="q", split="dev", question="1인 가구", measures=["가구"])
    assert ablation.gold_parsed(homes).measures == ["가구"]


class _Filterable(BaseRetriever):
    """A base with with_filter, like ChromaTableRetriever; remembers the filter it was given."""

    seen: list = []
    last_removed: dict = {}

    def with_filter(self, f: TableFilter, records):
        _Filterable.seen.append(f)
        return self

    def _get_relevant_documents(self, query, *, run_manager):
        return [Document(page_content="", metadata={"key": "101_DT_MAR"})]


@offline
def test_pred_slots_all_on_is_the_condition_retriever_filter():
    question = "2023년 서울 성별 혼인 건수"
    _Filterable.seen = []
    ConditionRetriever(base=_Filterable(), records=RECORDS).invoke(question)
    assert _Filterable.seen[0] == ablation.filter_for(parse_query(question), ablation.combos()["all"])


def _items():
    return [
        RetrievalItem(**{**BASE, "qid": "m"}, qtype="multi_condition", question="2023년 서울 혼인 건수", required=[["101_DT_MAR"]]),
        RetrievalItem(**{**BASE, "qid": "b"}, qtype="concept", question="출생아 수", required=[["101_DT_BIRTH"]]),
        RetrievalItem(**{**BASE, "qid": "p"}, qtype="no_answer", question="1990년 혼인 건수", answerable=False,
                      no_answer_reason="no_period"),
    ]


@offline
def test_ablation_without_search_scores_recall_and_slot_free_items_match_none():
    items = _items()
    slots = {i.qid: parse_query(i.question) for i in items}
    on = ablation.combos(["year", "region", "measure"])
    rows = ablation.run(items, slots, RECORDS, on)
    by = {(r["combo"], r["qid"]): r for r in rows}
    # "출생아 수" has no year and no region: switching those on changes nothing for it
    for c in ("only:year", "only:region"):
        assert by[(c, "b")]["n_candidates"] == by[("none", "b")]["n_candidates"] == 3
    assert by[("only:year", "m")]["n_candidates"] == 2  # 101_DT_OLD has no 2023
    assert by[("only:year", "p")]["n_candidates"] == 0  # no table holds 1990: refused by the filter
    s = ablation.summarize(rows, on, slots, tau=0.0)
    assert s["combos"]["all"]["filter_recall"] == 1.0
    assert s["combos"]["only:year"]["no_period_refusal"] == 1.0 and s["combos"]["none"]["no_period_refusal"] == 0.0
    assert s["combos"]["only:year"]["zero_no_answer"] == 1.0 and s["combos"]["none"]["zero_no_answer"] == 0.0
    assert s["combos"]["all"]["zero_answerable"] == 0.0 and s["combos"]["all"]["n_no_answer"] == 1
    assert s["slots"]["year"]["verdict"] == "불분명"  # no search: recall did not drop, help cannot be shown


@offline
@pytest.mark.parametrize("only, minus, expected", [
    ({"hit5_net": 2, "hit1_net": 0, "recall_delta": 0.0}, {"hit5_net": 2, "hit1_net": 1, "recall_delta": 0.0}, "도움"),
    ({"hit5_net": 1, "hit1_net": 0, "recall_delta": 0.0}, {"hit5_net": 3, "hit1_net": 0, "recall_delta": 0.0}, "불분명"),
    ({"hit5_net": 3, "hit1_net": -1, "recall_delta": 0.0}, {"hit5_net": 3, "hit1_net": 0, "recall_delta": 0.0}, "불분명"),
    ({"hit5_net": 3, "hit1_net": 0, "recall_delta": 0.0}, {"hit5_net": -1, "hit1_net": 0, "recall_delta": 0.0}, "해로움"),
    ({"hit5_net": 3, "hit1_net": 0, "recall_delta": -0.03}, {"hit5_net": 3, "hit1_net": 0, "recall_delta": 0.0}, "해로움"),
])
def test_slot_verdict_rule(only, minus, expected):
    assert ablation.verdict(only, minus) == expected


@offline
def test_l0_fill_rates_and_unmapped():
    s = metadata.score(RECORDS, gold={"101_DT_MAR"}, loaded={"101_DT_MAR", "101_DT_BIRTH"})
    assert s["all"]["fill"]["regions"] == 1.0 and s["all"]["regions_none"] == pytest.approx(1 / 3)  # OLD: national
    assert s["all"]["measure_unmapped"] == pytest.approx(1 / 3)  # "혼인 통계" does not say what it counts
    assert s["gold"]["fill"]["measure"] == 1.0
    assert s["load"]["missing"] == ["101_DT_OLD"] and not s["load"]["match"]
    acc = metadata.score(RECORDS, set(), sample={"101_DT_BIRTH": {"measure": "출생아수", "dimensions": ["시도"]}})["accuracy"]
    assert acc == {"measure": 1.0, "dimensions": 1.0}


@offline
def test_l012_runs_every_layer_once_and_keeps_ops_per_layer(monkeypatch, tmp_path):
    import json

    from evals import ops, run_eval

    seen = []
    fake = lambda name, latency: lambda args: (seen.append((name, args.slot_source, args.split))  # noqa: E731
                                               or ([{"qid": "q1", "combo": "all", "latency_ms": latency}], {"name": name}, f"# {name}\n"))
    monkeypatch.setattr(run_eval, "run_l0", fake("l0", 0.0))
    monkeypatch.setattr(run_eval, "run_ablation", lambda args: fake(f"l1-{args.slot_source}", 0.0)(args))
    monkeypatch.setattr(run_eval, "run_l2", fake("l2", 5.0))
    monkeypatch.setattr(run_eval, "REPORTS", tmp_path)
    monkeypatch.setattr(ops, "append_history", lambda run_id, layer, *a: seen.append(("history", layer)))
    assert run_eval.main(["--layer", "l012", "--stage", "t"]) == 0
    assert [s for s in seen if s[0] != "history"] == [("l0", "pred", "dev"), ("l1-gold", "gold", "dev"),
                                                      ("l1-pred", "pred", "dev"), ("l2", "pred", "dev")]
    assert [s[1] for s in seen if s[0] == "history"] == ["l0", "l1-gold", "l1-pred", "l2"]
    [out] = tmp_path.iterdir()
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert set(m["summary"]) == set(m["ops"]) == {"l0", "l1-gold", "l1-pred", "l2"} and set(m["golden"]) == {"l0·l1", "l2"}
    assert m["ops"]["l2"]["latency_ms"]["p50"] == 5.0  # not pulled down by the 0.0 rows of the other layers
    assert {json.loads(line)["layer"] for line in (out / "results.jsonl").open(encoding="utf-8")} == set(m["ops"])
