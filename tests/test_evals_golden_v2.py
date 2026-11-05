"""T2-32 golden v2 builder: L2 fields from facts, pool rule-out by year/label, no_answer terms checked."""

from kosis_ai.schemas import Fact

from evals.builders.golden_v2 import absent_ok, l2_from_facts, rule_out
from evals.schemas import CalcItem


def item(kind="answerable", type_="value", facts=None) -> CalcItem:
    return CalcItem(qid="V2-x", kind=kind, type=type_, question="q", table_key="101_T", table_sha256="s",
                    required_facts=facts or [], allowed_reasons=["no_period"] if kind != "answerable" else [])


def cell_fact(fid: str, period: str, dims: dict) -> Fact:
    return Fact(id=fid, value=1.0, period=period, dims=dims, source_ref=f"T|{fid}|{period}")


def test_l2_from_facts_value_trend_and_region_alias():
    value = item(facts=[cell_fact("f1", "2025", {"시도별": "강원도", "항목": "출생"})])
    assert l2_from_facts(value, "2025년 강원 출생은?") == {
        "period_start": "2025", "period_end": "2025", "years": [2025], "regions": ["강원특별자치도"], "cycle": None}
    trend = item(type_="trend", facts=[cell_fact("f1", "2015", {}), cell_fact("f2", "2024", {}),
                                       Fact(id="f3", value=2.0, derivation="f2 - f1", round=1)])
    got = l2_from_facts(trend, "2015년부터 2024년까지 연도별로 얼마나 변했어?")
    assert (got["period_start"], got["period_end"], got["years"], got["cycle"]) == ("2015", "2024", [], "Y")


def test_rule_out_needs_year_and_non_total_labels():
    calc = item(facts=[cell_fact("f1", "2020", {"행정구역별": "양천구", "성별": "계"})])
    meta = {"periods": ["2020"], "items": ["인구"], "classes": [{"name": "행정구역별", "labels": ["양천구", "계"]}]}
    assert rule_out(calc, meta) == ""
    assert rule_out(calc, meta | {"periods": ["2019"]}).startswith("rule:no_period")
    assert rule_out(calc, meta | {"classes": [{"name": "행정구역별", "labels": ["강서구"]}]}).startswith("rule:no_label")


def test_absent_ok_lists_tables_holding_all_terms():
    metas = {"a": {"search_text": "자동차 보유대수별 가구", "periods": ["2005"]}, "b": {"search_text": "혼인건수", "periods": ["2025"]}}
    assert absent_ok({"absent": ["자동차"]}, metas) == ["a"]
    assert absent_ok({"absent": ["혼인건수"], "year": "2030"}, metas) == []
    assert absent_ok({"absent": []}, metas)  # no terms is a problem, not a pass


META = {"info": {"table_name": "시도/인구동태건수", "survey_name": "인구동향조사", "menu_path": "인구 > 인구동향조사"},
        "items": ["출생건수 (명)", "조출생률 (천명당)", "사망건수 (명)"],
        "classes": [{"name": "행정구역별", "labels": ["전국", "서울특별시", "부산광역시"]}]}


def births() -> CalcItem:
    return item(facts=[cell_fact("f1", "2025", {"행정구역별": "서울특별시", "항목": "출생건수 (명)"})])


def test_user_phrase_must_pin_the_cell_against_sibling_labels():
    from evals.builders.golden_v2 import check_user_question

    t = "2025년 서울특별시 출생건수 (명)은?"
    assert check_user_question(births(), t, "2025년 서울에서 출생건수는?", {}, "출생건수 (명)", META) == []
    # '아이' fits 출생건수 and 조출생률 alike (USER_WORDS 출생): it does not pin the item
    assert check_user_question(births(), t, "2025년 서울 아이 얼마나 돼?", {"출생건수 (명)": "아이"}, "출생건수 (명)", META)
    assert check_user_question(births(), t, "2025년 서울에서 태어난 아이 건수는?", {"출생건수 (명)": "태어난 아이 건수"},
                               "출생건수 (명)", META) == []  # 건수 bigram tips it to 출생건수
    assert check_user_question(births(), t, "2025년 서울과 부산광역시 출생건수는?", {}, "출생건수 (명)", META)  # another cell
    assert check_user_question(births(), t, "2025년 서울 출생건수, 열두 살 기준", {}, "출생건수 (명)", META)  # Korean count


def test_carry_keeps_judgements_and_slotted_rows_that_left_the_top10():
    from evals.builders.golden_v2 import carry

    prev = [{"qid": "q", "key": "a", "slot": "1", "auto": "claude"}, {"qid": "q", "key": "b", "slot": "", "auto": "claude"},
            {"qid": "q", "key": "c", "slot": "1", "auto": "claude"}]
    new = [{"qid": "q", "key": "a", "slot": "", "auto": ""}, {"qid": "q", "key": "b", "slot": "", "auto": ""},
           {"qid": "q", "key": "d", "slot": "", "auto": ""}]
    out = {r["key"]: r for r in carry(prev, new)}
    assert (out["a"]["slot"], out["a"]["auto"]) == ("1", "claude") and out["b"]["auto"] == "claude"
    assert out["d"]["auto"] == "" and out["c"]["found_by"] == "carried" and out["c"]["slot"] == "1"
