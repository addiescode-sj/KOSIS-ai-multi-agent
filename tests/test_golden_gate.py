"""T3-47 e2e golden quality gate (plans/golden-quality-gate.md): duplicates, leakage shapes, coverage, review marks."""

from kosis_ai.schemas import Fact

from evals import golden_gate as gate
from evals.schemas import AgentScenario, CalcItem, TurnLabels


def scen(sid, *turns, split="test", notes="value"):
    return AgentScenario.model_validate({"sid": sid, "split": split, "notes": notes, "turns": [
        {"question": q, "expect": {"labels": labels.model_dump(exclude_none=True)}} for q, labels in turns]})


NEW = TurnLabels(reading="new", outcome="answered", qtype="value")
CARRY = TurnLabels(reading="carry_over", outcome="answered", qtype="value")


def test_duplicates_count_standalone_questions_not_context_dependent_follow_ups():
    rows = [scen("A", ("2025년 혼인 건수는?", NEW)), scen("B", ("2025년  혼인 건수는?", NEW)),  # same after normalising
            scen("C", ("2024년 출생아 수는?", NEW), ("부산 기준으로는?", CARRY)),
            scen("D", ("2024년 사망자 수는?", NEW), ("부산 기준으로는?", CARRY))]  # same words, other conversation
    d = gate.duplicates(rows)
    assert d["unintended"] == 1 and d["examples"] == [["A", "B"]]


def test_tuned_follow_up_shapes_are_found_whatever_the_year_or_region():
    shapes = gate.tuned_shapes()
    assert gate.tuned("아니 2019년으로", shapes)  # T3-27 CORRECTION template, other year
    assert gate.tuned("경남은?", shapes)  # ELLIPSIS template, other 시도
    assert gate.tuned("그 표에서 2020년 혼인 건수는?", shapes)  # reference phrase the rules key on
    assert not gate.tuned("2019년이면?", shapes) and not gate.tuned("부산 기준으로는?", shapes)  # T3-47 wordings


def test_coverage_counts_required_types_and_minimum_sample():
    rows = [scen(f"S{i}", ("q", TurnLabels(reading="new", outcome="clarify", judge_rule="same_name")), notes="same_name") for i in range(20)]
    rows += [scen("T", ("q", TurnLabels(reading="new", outcome="refused", refusal_reasons=["no_period"], judge_rule="partial_range")))]
    c = gate.coverage(rows)
    assert c["types"]["classify: same_name / vague_item 되묻기"] == 20
    assert c["types"]["classify: partial_range / no_cell"] == 1 and c["types"]["classify: no_period"] == 0  # partial_range apart
    assert c["below_min"]["classify: partial_range / no_cell"] == 1 and "qtype: share" in c["missing"]


def test_degenerate_items_have_no_discriminating_power():
    def item(type_, *values):
        facts = [Fact(id=f"f{n}", value=v, period="2025", source_ref=f"T|I|{n}|2025") for n, v in enumerate(values, 1)]
        return CalcItem(qid="q", kind="answerable", type=type_, question="q", table_key="k", table_sha256="s", required_facts=facts)

    assert gate.degenerate(item("compare", 0.0, 0.0)) and gate.degenerate(item("trend", 5.0, 5.0))
    assert not gate.degenerate(item("compare", 0.0, 3.0)) and not gate.degenerate(item("value", 0.0))


def test_new_type_turns_that_rules_disagree_with_wait_for_review():
    from evals.builders.e2e_golden import mark_review

    gold = TurnLabels(reading="new", outcome="clarify", judge_rule="same_name")
    rows = [AgentScenario.model_validate({"sid": sid, "notes": notes, "turns": [{"question": "2025년 중구 인구는?", "expect": {
        "labels": gold.model_dump(exclude_none=True), "rule_pred": pred}}]})
        for sid, notes, pred in [("A", "same_name", {"reading": "new", "outcome": "answered"}),
                                 ("B", "same_name", {"reading": "new", "outcome": "clarify", "judge_rule": "same_name"}),
                                 ("C", "value", {"reading": "new", "outcome": "answered"})]]
    marked = {s.sid: s.turns[0].expect.labels.review for s in mark_review(rows)}
    assert marked == {"A": "needs_review", "B": None, "C": None}  # only new types; agreeing ones need no person


def test_review_verdicts_are_applied_and_survive_a_rewrite(tmp_path):
    # T3-47 review: ok -> accepted, wrong -> out of the set; rewriting the CSV keeps every verdict and note
    from evals.builders import e2e_golden as b

    def s(sid, q, kind="share"):
        return AgentScenario.model_validate({"sid": sid, "notes": kind, "turns": [{"question": q, "expect": {
            "labels": TurnLabels(reading="new", outcome="answered", qtype="share", review="needs_review").model_dump(exclude_none=True)}}]})

    rows = [s("A", "2052년 추계가구 합계, 전체에서 부산광역시가 차지하는 비중은 몇 %야?"), s("B", "2010년, 전체에서 진천군이 차지하는 비중은?"),
            s("C", "2024년 남자 유학생, 전체에서 베트남이 차지하는 비중은 몇 %야?")]
    path = tmp_path / "review.csv"
    import csv

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sid", "kind", "conversation", "question", "verdict(ok/wrong)", "note"])
        w.writerow(["A", "share", rows[0].turns[0].question, "x", "ok", "맞음"])
        w.writerow(["B", "share", rows[1].turns[0].question, "x", "wrong", "지표 빠짐"])
    reviews = b.load_reviews(path)
    kept, rejected = b.apply_reviews(rows, reviews)
    assert [x.sid for x in kept] == ["A", "C"] and kept[0].turns[0].expect.labels.review == "accepted"
    assert rejected[0]["sid"] == "B" and rejected[0]["note"] == "지표 빠짐"
    b.write_review(kept, reviews, path)
    again = b.load_reviews(path)
    assert {k[1][:7]: v["verdict(ok/wrong)"] for k, v in again.items()} == {"2052년추계": "ok", "2010년전체": "wrong"}


def test_share_and_extreme_guards_from_the_review():
    from evals.builders.e2e_golden import measure_prefix, without_aggregates

    assert measure_prefix("2052년 추계가구 합계") and not measure_prefix("2010년") and not measure_prefix("2025년 서울 여자인구수")

    class C:
        def __init__(self, v):
            self.value = v

    parts = [C(36435), C(10000), C(18384), C(8051)]  # 세종시 = 동부 + 읍부 + 면부
    assert sorted(c.value for c in without_aggregates(parts)) == [8051, 10000, 18384]
