"""T2-34: rules the criteria doc asks for — L4 §6.3 연도·지역, §4.3 refusal by reason, §5 list-field P/R, owners."""

from kosis_ai.schemas import StatAnswer, StatQuery, StatResult

from evals.evaluators import query, retrieval
from evals.runners import e2e
from evals.schemas import QueryItem


def answered(periods: list[str], filters: dict[str, str]) -> StatAnswer:
    return StatAnswer.model_construct(outcome="answered", result=StatResult(), parts=[], clarify_options=[],
                                      default_applied={}, query=StatQuery.model_construct(question="q", periods=periods, filters=filters))


def gold(**kw) -> QueryItem:
    return QueryItem(qid="q", split="test", question="q", **kw)


def test_template_slots_match_l2_labels():
    g = gold(period_start="2015", period_end="2024", regions=["서울특별시"])
    assert e2e.template_slots(answered(["2015", "2020", "2024"], {"행정구역별": "서울"}), g) == {"year_ok": True, "region_ok": True}
    assert e2e.template_slots(answered(["2024"], {"행정구역별": "부산광역시"}), g) == {"year_ok": False, "region_ok": False}
    monthly = gold(period_start="2024", period_end="2024")  # a yearly label is met by the months of that year
    assert e2e.template_slots(answered(["202401", "202412"], {}), monthly) == {"year_ok": True, "region_ok": True}
    assert e2e.template_slots(answered(["2024"], {}), None) == {"year_ok": None, "region_ok": None}  # no L2 item: skipped
    assert e2e.template_slots(answered([], {}), gold())["year_ok"] is None  # the label names no period


def test_refusal_by_reason_counts_only_unanswerable_questions():
    rows = [{"qid": "a", "no_answer_pred": True}, {"qid": "b", "no_answer_pred": False}, {"qid": "c", "no_answer_pred": True}]
    got = retrieval.refusal_by_reason(rows, {"a": "no_period", "b": "no_period"})
    assert got == {"no_period": {"n": 2, "refused": 1, "rate": 0.5}}


def test_list_field_precision_recall():
    item = gold(regions=["서울특별시"], dimensions=["성", "연령"], years=[2020])
    oracle = {"regions": ["서울특별시"], "dimensions": ["성", "연령"], "years": [2020]}
    m = query.aggregate([{"split": "test", **query.score_query(item, oracle)}])["all"]
    assert all(m[f"{f}_{x}"] == 1.0 for f in query.PR_FIELDS for x in ("p", "r"))
    m = query.aggregate([{"split": "test", **query.score_query(item, {"regions": ["서울특별시", "부산광역시"], "dimensions": ["성"]})}])["all"]
    assert (m["regions_p"], m["regions_r"], m["dimensions_p"], m["dimensions_r"]) == (0.5, 1.0, 1.0, 0.5)


def test_owner_section_routes_failures_to_the_team_that_owns_them():
    from evals.run_eval import owner_section

    rows = [{"qid": "x", "verdict": "FAIL", "cause": "retrieval", "year_ok": None, "region_ok": None},
            {"qid": "y", "verdict": "FAIL", "cause": "answer", "year_ok": False, "region_ok": True},
            {"qid": "z", "verdict": "PASS", "cause": "pass", "year_ok": True, "region_ok": True}]
    text = owner_section("e2e", rows, {})
    assert "검색·답 없음 판정" in text and "검색 1위가 다른 표 1 (x)" in text
    assert "정답 표인데 답 틀림 1 (y)" in text and "답의 연도가 질문과 다름 1 (y)" in text and "(z" not in text
    l2 = owner_section("l2", [{"qid": "p", "regions": 0, "cycle": 1}], {})
    assert "데이터" in l2 and "parse_query regions 오답 1 (p)" in l2
    assert owner_section("l1", [{"qid": "ok", "answerable": True, "hit@5": 1, "no_answer_pred": False}], {}) == ""
