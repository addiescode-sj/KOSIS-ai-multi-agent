"""T3-02 chart kinds (plans/T3-02.md, option A): the AUTO_KIND table and the guard on a requested kind.

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
from kosis_chat.compute import build, compute
from kosis_chat.data.loader import load_table
from kosis_chat.data.preprocessing import clean

from kosis_agent.services.chart import AUTO_KIND, draw, has_time_axis, refine, to_vega_lite

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
                    {"grouped_bar", "multi_line"}),
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
    ("두 지역 격차 변화", "line", {"multi_line"}, False),
    ("두 지역 격차 변화", "pie", {"grouped_bar"}, True),
    # HELD
    ("두 시점 비교", "line", {"line"}, False),  # two periods of one region: a time axis
    ("구성비 1개", "bar", {"bar"}, False),
    ("세 지역 비교", "line", {"bar"}, True),
    ("최솟값 연도 3시점", "pie", {"line"}, True),
]


def _chart(table, name):
    qtype, built, _ = CASES[name]
    return chart_spec(qtype, compute(table, built()))


@offline
@pytest.mark.parametrize("name", list(CASES))
def test_the_automatic_kind_follows_the_rule(table, name):
    chart = _chart(table, name)
    assert refine(chart).kind in CASES[name][2]
    vega = to_vega_lite(chart)
    assert vega is not None and len(vega["data"]["values"]) >= len(chart.points())


@offline
@pytest.mark.parametrize("name, requested, accepted, refused", REQUESTS)
def test_a_requested_kind_the_data_cannot_carry_keeps_the_chart_and_says_why(table, name, requested, accepted, refused):
    out = draw(_chart(table, name), requested)
    assert out["kind"] in accepted and out["vega_lite"] is not None
    assert (out["note"] is not None) == refused


@offline
def test_the_table_changes_only_the_kind_never_a_number(table):
    for name in CASES:
        chart = _chart(table, name)
        refined = refine(chart)
        assert [(p.label, p.value, p.display, p.fact_id) for p in refined.points()] == \
               [(p.label, p.value, p.display, p.fact_id) for p in chart.points()]
        assert refined.fact_ids == chart.fact_ids
        if refined.kind != chart.kind:
            assert (chart.pattern, chart.kind) in AUTO_KIND and refined.vega_lite is None


@offline
def test_a_stacked_bar_is_each_share_and_its_rest_to_100(table):
    chart = _chart(table, "구성비 변화 2부분")
    rows = to_vega_lite(chart)["data"]["values"]
    by_period = {}
    for row in rows:
        by_period.setdefault(row["시점"], []).append(row["값"])
    assert len(by_period) == 2 and all(round(sum(v), 6) == 100 for v in by_period.values())
    assert [r["값"] for r in rows if r["구분"] != "그 외"] == [p.value for p in chart.points()]  # the share facts as they are


@offline
def test_a_share_that_is_not_a_percent_is_not_stacked(table):
    chart = _chart(table, "구성비 변화")
    assert refine(chart.model_copy(update={"unit": "가구"})).kind == "bar"


@offline
def test_a_line_over_two_compared_periods_uses_the_period_labels(table):
    chart = _chart(table, "두 시점 비교")
    assert has_time_axis(chart) and not has_time_axis(_chart(table, "두 지역 비교"))
    rows = draw(chart, "line")["vega_lite"]["data"]["values"]
    assert [r["시점"] for r in rows] == ["2015", "2023"]
