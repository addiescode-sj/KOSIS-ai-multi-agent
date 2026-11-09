"""T3-27: the multi-turn dev scenario builder and the golden lookup search of the dev agent runner."""

from pathlib import Path

import pytest

from evals.builders import multiturn
from evals.evaluators import calculation
from evals.runners.multiturn_dev import DISTRACTOR, GoldenLookupSearch
from evals.schemas import DATASETS, AgentScenario
from kosis_rag.schemas.io import SearchInput

ROOT = Path(__file__).resolve().parents[1]
DATA = (ROOT / "data" / "values").is_dir() and (DATASETS / "calculation_golden.jsonl").is_file()


def test_topic_particle():
    assert multiturn.eun("부산") == "은" and multiturn.eun("세종") == "은" and multiturn.eun("대구") == "는"
    assert multiturn.eun("2024년") == "은"


def test_the_lookup_finds_a_golden_question_inside_a_longer_one(tmp_path):
    golden = tmp_path / "calc.jsonl"
    golden.write_text('{"question": "2020년 부산광역시 인구밀도는?", "table_key": "101_DT_A"}\n'
                      '{"question": "인구밀도는?", "table_key": "101_DT_B"}\n', encoding="utf-8")
    search = GoldenLookupSearch(golden)
    assert search.key_for("2020년 부산광역시 인구밀도는? 막대그래프로도 그려줘") == "101_DT_A"  # longest match wins
    assert search.key_for("부산은?") == DISTRACTOR  # a fragment gets an unrelated table, as live search often does
    ((name, out),) = list(search.stream(SearchInput(question="부산은?")))
    assert name == "output" and out.route == "found" and out.picked[0].key == DISTRACTOR


@pytest.fixture(scope="module")
def built():
    if not DATA:
        pytest.skip("needs data/values and evals/datasets/calculation_golden.jsonl")
    return multiturn.build()  # reads the golden tables' Parquet files once for the tests below


def test_the_builder_makes_every_kind_and_reads_answers_from_the_parquet_files(built):
    scenarios, made = built
    kinds = {s.notes for s in scenarios}
    assert kinds == {"R", "E", "X", "S", "C", "M"}
    by_qid = {c.qid: c for c in made}
    for s in scenarios:
        if s.notes in ("E", "X"):
            last = s.turns[-1].expect
            item = by_qid[last.calc_qid]
            fact = item.required_facts[0]
            cell = calculation.read_cells(ROOT / "data" / "values" / f"{item.table_key}.parquet")[fact.source_ref]
            assert cell.value == fact.value  # the answer is the cell, not a number written by hand
            if s.notes == "E":
                assert last.tools[0].args["regions"][0] in multiturn.SIDO
    structure = multiturn.without_calc(scenarios[0])
    assert all(t.expect.calc_qid is None for t in structure.turns) and isinstance(structure, AgentScenario)


def test_excluded_items_make_no_ellipsis_or_correction_scenarios(built):
    _, made = built
    sources = {c.qid.split("-", 2)[2].rsplit("-", 1)[0] for c in made}
    assert not sources & set(multiturn.EXCLUDE)
