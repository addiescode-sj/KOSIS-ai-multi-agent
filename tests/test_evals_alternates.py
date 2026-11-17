"""Alternate gold tables by cell values (plans/T3-41.md): the match rule, the remapped grading of an answer read from an
alternate table, and that answers from the golden table (L3) grade exactly as before. Offline, tiny Parquet files."""

import json

import pyarrow as pa
import pyarrow.parquet as pq
from kosis_ai.schemas import Cell, Fact, StatAnswer

from evals.evaluators import alternates
from evals.runners import e2e
from evals.schemas import CalcItem
from evals.tasks import common

DIMS = [f"dim{i}_{k}" for i in range(1, 9) for k in ("name", "code", "label")]


def cell_row(tbl, item, item_name, dims, period, value, unit=None):
    row = {"org_id": "101", "tbl_id": tbl, "source": "api", "prd_se": "Y", "period": period, "item_id": item,
           "item_name": item_name, "unit": unit, "value_raw": str(value), "value": float(value), "value_kind": "ok",
           **dict.fromkeys(DIMS)}
    for i, (name, code, label) in enumerate(dims, 1):
        row.update({f"dim{i}_name": name, f"dim{i}_code": code, f"dim{i}_label": label})
    return row


def cell(*args, **kw) -> Cell:
    return Cell.from_row(cell_row(*args, **kw))


SEOUL = ("시도별", "11", "서울특별시")
BUSAN = ("시도별", "21", "부산광역시")
GOLD = [Fact(id="f1", value=1627480, unit="가구", period="2023", dims={"행정구역별": "서울특별시", "항목": "1인"},
             source_ref="DT_G|T1|11|2023"),
        Fact(id="f2", value=4141659, unit="가구", period="2023", dims={"행정구역별": "서울특별시", "항목": "일반가구_계"},
             source_ref="DT_G|T0|11|2023"),
        Fact(id="f3", value=39.3, unit="%", derivation="f1 / f2 * 100", round=1)]


def item(**kw) -> CalcItem:
    return CalcItem(**{"qid": "q1", "kind": "answerable", "question": "2023년 서울 일반가구 중 1인 가구 비중은?",
                       "table_key": "101_DT_G", "table_sha256": "x", "required_facts": GOLD, **kw})


def alt_cells(**over):
    """An alternate table: households by size with a total breakdown column."""
    rows = [cell("DT_A", "A1", "1인가구 (가구)", [SEOUL, ("세대구성별", "0", "계")], "2023", 1627480),
            cell("DT_A", "A0", "일반가구 (가구)", [SEOUL, ("세대구성별", "0", "계")], "2023", 4141659)]
    return rows


def test_match_moves_every_cell_fact_to_the_alternate_table():
    refs = alternates.match(item(), alt_cells())
    assert refs == {"f1": "DT_A|A1|11|0|2023", "f2": "DT_A|A0|11|0|2023"}
    moved = alternates.remapped(item(), "101_DT_A", refs)
    assert moved.table_key == "101_DT_A" and moved.required_facts[0].value == 1627480  # golden values kept
    assert moved.required_facts[2].derivation == "f1 / f2 * 100"  # derived facts follow by id


def test_match_rejects_same_value_with_another_meaning():
    base = alt_cells()
    other_region = [cell("DT_A", "A1", "1인가구", [BUSAN], "2023", 1627480), base[1]]
    assert alternates.match(item(), other_region) is None  # same number, another 시도
    breakdown = [cell("DT_A", "A1", "1인가구", [SEOUL, ("성별", "1", "남자")], "2023", 1627480), base[1]]
    assert alternates.match(item(), breakdown) is None  # a non-total breakdown (남자) is not the golden cell
    unit = [cell("DT_A", "A1", "1인가구", [SEOUL], "2023", 1627480, unit="명"), base[1]]
    assert alternates.match(item(), unit) is None  # 명 vs 가구
    period = [cell("DT_A", "A1", "1인가구", [SEOUL], "2022", 1627480), base[1]]
    assert alternates.match(item(), period) is None
    assert alternates.match(item(), base[:1]) is None  # every cell fact must be there


def test_facts_of_one_golden_item_stay_on_one_item():
    facts = [Fact(id="f1", value=500, period="2023", dims={"행정구역별": "서울특별시"}, source_ref="DT_G|T1|11|2023"),
             Fact(id="f2", value=600, period="2024", dims={"행정구역별": "서울특별시"}, source_ref="DT_G|T1|11|2024")]
    it = item(required_facts=facts)
    split = [cell("DT_A", "X", "출생", [SEOUL], "2023", 500), cell("DT_A", "Y", "사망", [SEOUL], "2024", 600)]
    assert alternates.match(it, split) is None  # 2023 births and 2024 deaths are not one series
    same = [cell("DT_A", "X", "출생", [SEOUL], "2023", 500), cell("DT_A", "X", "출생", [SEOUL], "2024", 600)]
    assert alternates.match(it, same) is not None


def test_a_national_fact_needs_a_national_cell_and_cited_cells_win():
    facts = [Fact(id="f1", value=700, period="2023", dims={"항목": "출생건수"}, source_ref="DT_G|T1|00|2023")]
    it = item(required_facts=facts)
    assert alternates.match(it, [cell("DT_A", "X", "출생", [SEOUL], "2023", 700)]) is None
    two = [cell("DT_A", "X", "출생", [("시도별", "00", "전국")], "2023", 700), cell("DT_A", "Z", "출생", [], "2023", 700)]
    assert alternates.match(it, two, cited=frozenset({"DT_A|Z|2023"})) == {"f1": "DT_A|Z|2023"}


def write_table(root, key, cells):
    rows = [{**cell_row("x", "x", "x", [], "x", 0), **{k: v for k, v in c.items()}} for c in cells]
    (root / "data" / "values").mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), root / "data" / "values" / f"{key}.parquet")


def answer_from(tbl, refs):
    facts = [{"id": "f1", "value": 1627480.0, "unit": "가구", "period": "2023", "source_ref": refs[0],
              "dims": {"시도별": "서울특별시", "가구원수별": "1인"}},  # '1인' lets the text say "1인 가구"
             {"id": "f2", "value": 4141659.0, "unit": "가구", "period": "2023", "source_ref": refs[1]},
             {"id": "f3", "value": 39.3, "unit": "%", "derivation": "f1 / f2 * 100", "round": 1}]
    return StatAnswer.model_validate({
        "outcome": "answered", "text": "2023년 서울 1인 가구 비중은 39.3%입니다. 출처: 인구총조사 / 표",
        "result": {"facts": facts},
        "parts": [{"kind": "text", "text": "2023년 서울 1인 가구 비중은 "}, {"kind": "fact", "fact_id": "f3"}, {"kind": "text", "text": "입니다."}],
        "source": {"org_id": "101", "tbl_id": tbl, "survey_nm": "인구총조사", "tbl_nm": "표"}})


def test_grading_reads_the_alternate_tables_cells_and_l3_is_unchanged(tmp_path, monkeypatch):
    write_table(tmp_path, "101_DT_A", [cell_row("DT_A", "A1", "1인가구 (가구)", [SEOUL], "2023", 1627480),
                                       cell_row("DT_A", "A0", "일반가구 (가구)", [SEOUL], "2023", 4141659)])
    write_table(tmp_path, "101_DT_G", [cell_row("DT_G", "T1", "1인", [("행정구역별", "11", "서울특별시")], "2023", 1627480),
                                       cell_row("DT_G", "T0", "일반가구_계", [("행정구역별", "11", "서울특별시")], "2023", 4141659)])
    monkeypatch.setattr(common, "ROOT", tmp_path)
    monkeypatch.setattr(alternates, "VALUES", tmp_path / "data" / "values")
    alt = answer_from("DT_A", ["DT_A|A1|11|2023", "DT_A|A0|11|2023"])
    gold = answer_from("DT_G", ["DT_G|T1|11|2023", "DT_G|T0|11|2023"])
    [a, g] = common.grade([item(), item()], [{"answer": alt, "error": None, "used_table": "101_DT_A"},
                                             {"answer": gold, "error": None, "used_table": "101_DT_G"}])
    assert a["verdict"] == "PASS", (a["errors"], a["details"], a["graded_table"])
    assert a["graded_table"] == "101_DT_A" and a["table_key"] == "101_DT_G"
    assert g["verdict"] == "PASS" and g["graded_table"] == "101_DT_G"  # golden table: the old path
    wrong = answer_from("DT_A", ["DT_A|A1|11|2023", "DT_A|A0|11|2023"]).model_copy(deep=True)
    wrong.result.facts[0].value = 1627481.0  # one off: the alternate does not rescue a wrong number
    [w] = common.grade([item()], [{"answer": wrong, "error": None, "used_table": "101_DT_A"}])
    assert w["verdict"] == "FAIL"


def test_scan_labels_and_self_check(tmp_path):
    write_table(tmp_path, "101_DT_A", [cell_row("DT_A", "A1", "1인가구", [SEOUL], "2023", 1627480),
                                       cell_row("DT_A", "A0", "일반가구", [SEOUL], "2023", 4141659)])
    write_table(tmp_path, "101_DT_G", [cell_row("DT_G", "T1", "1인", [("행정구역별", "11", "서울특별시")], "2023", 1627480),
                                       cell_row("DT_G", "T0", "일반가구_계", [("행정구역별", "11", "서울특별시")], "2023", 4141659)])
    lines = alternates.scan([item()], tmp_path / "data" / "values")
    assert {"qid": "q1", "table_key": "101_DT_G", "self_check": True} in lines
    [label] = [d for d in lines if "status" in d]
    assert label["table_key"] == "101_DT_A" and label["status"] == "auto"
    path = tmp_path / "alts.jsonl"
    path.write_text("".join(json.dumps(d) + "\n" for d in lines), encoding="utf-8")
    assert alternates.load(path) == {"q1": {"101_DT_A"}}
    assert alternates.status(item(required_facts=[GOLD[0].model_copy(update={"value": 39.3})])) == "needs_review"


def test_locate_counts_labelled_alternates_as_right():
    row = {"keys": ["101_DT_A", "101_DT_G"], "used_table": "101_DT_A"}
    assert not e2e.locate(item(), row, "PASS")["right_table"]
    found = e2e.locate(item(), row, "PASS", alts={"101_DT_A"})
    assert found["right_table"] and found["right_alternate"] and found["alt_linked"] and found["gold_rank"] == 1
