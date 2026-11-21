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
