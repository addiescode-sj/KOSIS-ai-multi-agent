"""T3-02 chart kinds (plans/T3-02.md): the automatic table and the guard on a requested kind, now in the phase 1 chart
module (T1-32 kosis_chat.chart.module make_chart / redraw); services/chart.py is the shell this checks end to end.

Charts come from real phase 1 computations on phase 1's made-up household table (vendor/phase1/tests/conftest.py), not
from golden questions. Each case carries the kind the T3-02 rule asks for (time change = line, item comparison = bar,
composition = pie, composition change = stacked bar). DESIGN cases were looked at while writing the rule (a rule
conformance check); HELD cases were written down before the rule and only measured.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import kosis_chat
from kosis_chat.chart.builders import chart_spec
from kosis_chat.chart.module import make_chart
from kosis_chat.compute import build, compute
from kosis_chat.data.loader import load_table
from kosis_chat.data.preprocessing import clean

from kosis_agent.services.chart import chart_payload, draw

offline = pytest.mark.offline
P3, P4 = ("2015", "2020", "2023"), ("2015", "2020", "2023", "2024")


def _phase1_conftest():
    path = Path(kosis_chat.__file__).resolve().parents[1] / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location("phase1_conftest", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def table(tmp_path_factory):
    p1 = _phase1_conftest()
    folder = tmp_path_factory.mktemp("data")
    path = p1.write_table(folder, "FAKE_TBL_002", p1.household_rows(), {
        "survey_name": "가상 총조사", "organization": "가상 기관", "table_name": "가상 가구원수별 가구",
        "period": "년 (2015 ~ 2025)", "url": "https://example.invalid/fake-table-002"})
    return clean(load_table(path))


def ref(item, region, period):
    return f"FAKE_TBL_002|{item}|{region}|{period}"


def share_at(parts, region, period):
    return [ref(p, region, period) for p in parts], ref("T0", region, period)


# name -> (question type, plan builder, kinds the rule accepts)
CASES = {
    # DESIGN
    "한 값": ("value", lambda: build.value(ref("T1", "11", "2023")), {"bar", "timeline"}),
    "추이 3시점": ("trend", lambda: build.trend([ref("T1", "11", p) for p in P3]), {"line"}),
    "추이 4시점": ("trend", lambda: build.trend([ref("T1", "21", p) for p in P4]), {"line"}),
    "두 지역 비교": ("compare", lambda: build.compare([ref("T1", "11", "2023"), ref("T1", "21", "2023")], 1), {"bar"}),
    "두 시점 비교": ("compare", lambda: build.compare([ref("T1", "11", "2015"), ref("T1", "11", "2023")], 1), {"bar"}),
    "지역 최댓값": ("extreme", lambda: build.extreme([ref("T1", r, "2023") for r in ("11", "21", "39")], "max"), {"bar"}),
    "최댓값 연도": ("extreme", lambda: build.extreme([ref("T1", "11", p) for p in P4], "max"), {"line"}),
    "구성비 1개": ("share", lambda: build.share(*share_at(["T1"], "11", "2023")), {"pie"}),
    "구성비 2개": ("share", lambda: build.share(*share_at(["T1", "T2"], "11", "2023")), {"pie"}),
    "구성비 변화": ("share", lambda: build.share_change(share_at(["T1"], "11", "2015"), share_at(["T1"], "11", "2023")),
               {"stacked_bar"}),
    "기간 평균": ("value", lambda: build.over_periods([ref("T1", "11", p) for p in P3], "avg", "value"), {"line"}),
    "두 지역 격차 변화": ("compare", lambda: build.gap((ref("T1", "11", "2015"), ref("T1", "21", "2015")),
                                                (ref("T1", "11", "2023"), ref("T1", "21", "2023"))),
                    {"bar"}),  # T1-32: the gaps themselves (the answer's numbers), not the grid of levels
    "두 지역 증감률": ("compare", lambda: build.growth((ref("T1", "11", "2015"), ref("T1", "11", "2023")),
                                               (ref("T1", "21", "2015"), ref("T1", "21", "2023"))), {"bar", "grouped_bar"}),
    "평균 대비": ("compare", lambda: build.vs_average([ref("T1", "11", p) for p in ("2015", "2020")], ref("T1", "11", "2023")),
              {"bar"}),
    "기간 묶음 비교": ("compare", lambda: build.groups_compared([ref("T1", "11", "2015"), ref("T1", "11", "2020")],
                                                       [ref("T1", "11", "2023"), ref("T1", "11", "2024")], "avg"), {"bar"}),
    # HELD
    "최솟값 연도 3시점": ("extreme", lambda: build.extreme([ref("T2", "21", p) for p in P3], "min"), {"line"}),
    "비중 순위": ("extreme", lambda: build.extreme([ref("T1", r, "2023") for r in ("11", "21")], "max",
                                               totals=[ref("T0", r, "2023") for r in ("11", "21")]), {"bar"}),
    "구성비 변화 2부분": ("share", lambda: build.share_change(share_at(["T1", "T2"], "21", "2015"),
                                                       share_at(["T1", "T2"], "21", "2023")), {"stacked_bar"}),
    "다른 지역 기간 평균": ("value", lambda: build.over_periods([ref("T2", "21", p) for p in P4], "avg", "value"), {"line"}),
    "세 지역 비교": ("extreme", lambda: build.extreme([ref("T2", r, "2020") for r in ("11", "21", "39")], "min"), {"bar"}),
}
HELD = {"최솟값 연도 3시점", "비중 순위", "구성비 변화 2부분", "다른 지역 기간 평균", "세 지역 비교"}

# (case, requested kind, kinds accepted, refused with a note)
REQUESTS = [
    ("추이 4시점", "bar", {"timeline"}, False),
    ("추이 4시점", "pie", {"line"}, True),  # years are not the parts of one total
    ("두 지역 비교", "line", {"bar"}, True),  # no time axis
    ("두 지역 비교", "pie", {"bar"}, True),
    ("구성비 2개", "bar", {"bar"}, False),
    ("구성비 2개", "line", {"pie"}, True),
    ("구성비 2개", "pie", {"pie"}, False),
    ("지역 최댓값", "pie", {"bar"}, True),
    ("두 지역 격차 변화", "line", {"line"}, False),  # the gap at two periods: a time axis
    ("두 지역 격차 변화", "pie", {"bar"}, True),
    # HELD
    ("두 시점 비교", "line", {"line"}, False),  # two periods of one region: a time axis
    ("구성비 1개", "bar", {"bar"}, False),
    ("세 지역 비교", "line", {"bar"}, True),
    ("최솟값 연도 3시점", "pie", {"line"}, True),
]


def _computed(table, name):
    qtype, built, _ = CASES[name]
    return qtype, compute(table, built())


def _chart(table, name):
    """The chart phase 1 gives with the answer (no question: the data's shape decides)."""
    qtype, computed = _computed(table, name)
    return make_chart(None, computed, qtype).spec


@offline
@pytest.mark.parametrize("name", list(CASES))
def test_the_automatic_kind_follows_the_rule(table, name):
    chart = _chart(table, name)
    assert chart.kind in CASES[name][2]
    data = chart_payload(chart)
    assert data is not None and "vega_lite" not in data and len(data["series"][0]["points"]) >= 1


@offline
@pytest.mark.parametrize("name, requested, accepted, refused", REQUESTS)
def test_a_requested_kind_the_data_cannot_carry_keeps_the_chart_and_says_why(table, name, requested, accepted, refused):
    out = draw(_chart(table, name), requested)
    assert out["kind"] in accepted and out["chart"] is not None
    assert (out["note"] is not None) == refused


@offline
def test_the_module_changes_only_the_kind_never_a_number(table):
    for name in CASES:
        qtype, computed = _computed(table, name)
        built = chart_spec(qtype, computed)
        chart = _chart(table, name)
        facts = {f.id: f.value for f in computed.result.facts}
        assert all(facts[p.fact_id] == p.value for p in chart.points() if p.fact_id)
        if chart.pattern not in ("grw", "gap"):  # those draw their own builder, not the grid of levels
            assert [(p.value, p.fact_id) for p in chart.points()] == [(p.value, p.fact_id) for p in built.points()]


@offline
def test_a_stacked_bar_is_each_share_and_its_rest_to_100(table):
    chart = _chart(table, "구성비 변화 2부분")
    share, rest = chart_payload(chart)["series"]
    assert len(share["points"]) == 2 and rest["name"] == "그 외"
    assert all(round(a["value"] + b["value"], 6) == 100 for a, b in zip(share["points"], rest["points"]))
    assert [p["value"] for p in share["points"]] == [p.value for p in chart.points()]  # the share facts as they are


@offline
def test_a_share_that_is_not_a_percent_is_not_stacked(table):
    chart = _chart(table, "구성비 변화")
    assert chart.kind == "stacked_bar"
    out = draw(chart.model_copy(update={"unit": "가구"}), "stacked")
    assert out["kind"] == "bar" and "비중" in out["note"]


@offline
def test_a_line_over_two_compared_periods_uses_the_period_labels(table):
    chart = _chart(table, "두 시점 비교")
    out = draw(chart, "line")
    assert out["kind"] == "line" and [p["period"] for p in out["chart"]["series"][0]["points"]] == ["2015", "2023"]
    assert draw(_chart(table, "두 지역 비교"), "line")["kind"] == "bar"


def test_a_stacked_bar_is_asked_only_with_a_chart_word():
    from kosis_agent.graph.context import chart_request

    assert chart_request("누적 막대그래프로 그려줘") == (True, "stacked")
    assert chart_request("2023년 누적 확진자 수는?") == (False, None)  # 누적 alone is a number, not a chart
