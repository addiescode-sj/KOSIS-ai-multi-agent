"""T3-56: multi-table calc items (table_keys) and the human e2e golden builder."""

import pyarrow as pa
import pyarrow.parquet as pq
from kosis_ai.schemas import Fact, FactPart, StatAnswer, StatResult

from evals.builders import e2e_human
from evals.evaluators import calculation
from evals.schemas import CalcItem


def row(tbl: str, item: str, label: str, period: str, value: float, item_name: str = "인구") -> dict:
    return {"org_id": "101", "tbl_id": tbl, "source": "api", "prd_se": "Y", "period": period, "item_id": item,
            "item_name": item_name, "unit": "명", "dim1_name": "시도별", "dim1_code": "11", "dim1_label": label,
            "value_raw": str(value), "value": value, "value_kind": "ok"}


def write(folder, key: str, rows: list[dict]) -> None:
    pq.write_table(pa.Table.from_pylist(rows), folder / f"{key}.parquet")


BIRTH, DEATH = "DT_B|T1|11|2025", "DT_D|T1|11|2025"
ITEM = CalcItem(qid="H1", kind="answerable", question="q", table_key="101_DT_B", table_sha256="0",
                table_keys=["101_DT_B", "101_DT_D"],
                required_facts=[Fact(id="f1", value=100.0, source_ref=BIRTH, unit="명"),
                                Fact(id="f2", value=130.0, source_ref=DEATH, unit="명"),
                                Fact(id="f3", value=-30.0, unit="명", derivation="f1 - f2", round=0)])


def answer(*facts: Fact) -> StatAnswer:
    return StatAnswer.model_construct(outcome="answered", result=StatResult(facts=list(facts)),
                                      parts=[FactPart(fact_id=f.id) for f in facts], text="", refusal=None,
                                      clarify_options=[], default_applied={}, source=None)


def test_a_multi_table_item_reads_cells_from_every_table(tmp_path):
    write(tmp_path, "101_DT_B", [row("DT_B", "T1", "서울", "2025", 100.0)])
    write(tmp_path, "101_DT_D", [row("DT_D", "T1", "서울", "2025", 130.0)])
    cells_of = lambda key, refs: calculation.read_cells(tmp_path / f"{key}.parquet", refs)  # noqa: E731
    one = answer(Fact(id="f1", value=100.0, source_ref=BIRTH, unit="명"), Fact(id="f2", value=130.0, source_ref=DEATH, unit="명"),
                 Fact(id="f3", value=-30.0, unit="명", derivation="f1 - f2", round=0))
    cells = calculation.item_cells(ITEM, one, cells_of)
    assert set(cells) == {BIRTH, DEATH}
    assert calculation.grade(ITEM, one, cells, cite=False).verdict == "PASS"

    # per-table sub-answers (today's agent): both cells, but nobody computed the cross-table f3
    subs = calculation.merge_answers([answer(Fact(id="f1", value=100.0, source_ref=BIRTH, unit="명")),
                                      answer(Fact(id="f1", value=130.0, source_ref=DEATH, unit="명"))])
    assert [f.id for f in subs.result.facts] == ["s0_f1", "s1_f1"]  # ids kept apart
    g = calculation.grade(ITEM, subs, calculation.item_cells(ITEM, subs, cells_of), cite=False)
    assert g.verdict == "FAIL" and g.errors == ["wrong_dims"]


def test_merge_answers_rewrites_derivations_per_answer():
    a = answer(Fact(id="f1", value=4.0, source_ref="DT_B|T1|11|2024"), Fact(id="f2", value=5.0, source_ref=BIRTH),
               Fact(id="f3", value=25.0, derivation="(f2 / f1 - 1) * 100", round=1))
    merged = calculation.merge_answers([a, answer(Fact(id="f1", value=130.0, source_ref=DEATH))])
    assert merged.result.facts[2].derivation == "(s0_f2 / s0_f1 - 1) * 100"
    assert [p.fact_id for p in merged.parts] == ["s0_f1", "s0_f2", "s0_f3", "s1_f1"]
    assert calculation.merge_answers([a]) is a


def test_builder_matches_labels_to_cells_and_keeps_tables_apart(tmp_path, monkeypatch):
    write(tmp_path, "101_DT_B", [row("DT_B", "T1", "서울특별시", "2025", 1.2421), row("DT_B", "T1", "부산광역시", "2025", 1.3)])
    write(tmp_path, "101_DT_D", [row("DT_D", "T1", "서울특별시", "2025", 130.0, item_name="사망자수")])
    monkeypatch.setattr(e2e_human, "VALUES", tmp_path)
    base = {"qid": "H1", "role": "required", "raw": "", "derivation": "", "round": "", "note": "", "unit": ""}
    facts = [base | {"fact_id": "f1", "table_key": "101_DT_B", "item": "인구", "dims": "시도별=서울특별시",
                     "period": "2025", "value": "1.24"},  # a display-rounded value
             base | {"fact_id": "f2", "table_key": "101_DT_D", "item": "사망자수", "dims": "시도별 = 서울특별시",
                     "period": "2025", "value": "130"},
             base | {"fact_id": "f3", "table_key": "", "item": "", "dims": "", "period": "", "value": "-128.76",
                     "derivation": "f1 - f2", "round": "2"},
             base | {"qid": "H2", "fact_id": "f1", "table_key": "101_DT_B", "item": "인구", "dims": "시도별=대구광역시",
                     "period": "2025", "value": "9"}]
    q = {"split": "test", "kind": "answerable", "type": "compare", "refusal_reason": "", "allowed_reasons": "",
         "default_applied": "", "notes": "", "question": "q"}
    questions = [q | {"qid": "H1", "table_keys": "101_DT_B;101_DT_D"}, q | {"qid": "H2", "table_keys": "101_DT_B"}]
    scenarios, calc, problems = e2e_human.build(questions, facts)
    (item,) = calc
    assert item.table_keys == ["101_DT_B", "101_DT_D"]
    assert [f.source_ref for f in item.required_facts] == ["DT_B|T1|11|2025", DEATH, None]
    assert item.required_facts[0].value == 1.2421  # the cell's value, not the csv's rounding
    assert problems == ["H2 f1: 0 cells (101_DT_B 인구 시도별=대구광역시 2025 = 9)"]
    assert scenarios[1].turns[0].expect.calc_qid is None  # nothing left to grade
