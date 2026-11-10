"""T3-17 fetch_table rules on small synthetic Parquet tables (no provided data needed)."""

from __future__ import annotations

import csv

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kosis_agent.services.parquet_repository import AMBIGUOUS, SOURCE_COLUMNS, TableTooLarge, fetch_table

pytestmark = pytest.mark.offline


def _row(**kw) -> dict:
    row = dict.fromkeys(SOURCE_COLUMNS)
    row.update(prd_se="Y", period="2020", item_id="T1", item_name="인구", unit="명",
               dim1_name="성별", dim1_code="0", dim1_label="계", value_raw="10", value=10.0, value_kind="ok")
    row.update(kw)
    return row


def _write(tmp_path, rows, key="101_DT_TEST"):
    (tmp_path / "values").mkdir(exist_ok=True)
    schema = pa.schema([(c, pa.float64() if c == "value" else pa.string()) for c in SOURCE_COLUMNS])
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), tmp_path / "values" / f"{key}.parquet")
    with (tmp_path / "tables.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, ["key", "tbl_id", "survey_name", "organization", "table_name", "parquet", "url"])
        w.writeheader()
        w.writerow({"key": key, "tbl_id": key.split("_", 1)[1], "survey_name": "테스트조사", "organization": "기관",
                    "table_name": "테스트 표", "parquet": f"values/{key}.parquet", "url": "http://kosis.kr/x"})
    return key


def test_cycle_code_is_part_of_the_cell_key(tmp_path):
    # monthly 1970-01 and quarterly 1970 Q1 share the period string "197001"
    key = _write(tmp_path, [_row(prd_se="M", period="197001", value_raw="1", value=1.0),
                            _row(prd_se="Q", period="197001", value_raw="3", value=3.0)])
    t = fetch_table(key, data_dir=tmp_path)
    assert t.frame["cell_key"].nunique() == 2
    assert t.issues["ambiguous_cells"] == 0
    assert sorted(t.frame["value"]) == [1.0, 3.0]


def test_exact_duplicates_drop_and_conflicting_values_are_ambiguous(tmp_path):
    key = _write(tmp_path, [_row(), _row(),  # exact duplicate -> one row
                            _row(item_id="T2", value_raw="5", value=5.0), _row(item_id="T2", value_raw="7", value=7.0)])
    t = fetch_table(key, data_dir=tmp_path)
    assert t.issues["exact_duplicates_dropped"] == 1
    amb = t.frame[t.frame["item_id"] == "T2"]
    assert (amb["value_flag"] == AMBIGUOUS).all() and amb["value"].isna().all()
    assert sorted(amb["value_raw"]) == ["5", "7"]  # raw text kept, no number picked
    assert t.issues["ambiguous_keys"] == 1


def test_five_year_cycle_is_kept(tmp_path):
    key = _write(tmp_path, [_row(prd_se="F", period="2015"), _row(prd_se="F", period="2020")])
    t = fetch_table(key, data_dir=tmp_path)
    assert t.meta.prd_se == ["F"] and set(t.frame["prd_se"]) == {"F"}


def test_unknown_unit_is_not_invented_and_unit_in_name_is_found(tmp_path):
    key = _write(tmp_path, [_row(unit=None, item_name="출생건수 (명)", item_id="T1"),
                            _row(unit=None, item_name="지수", item_id="T2")])
    t = fetch_table(key, data_dir=tmp_path).frame.set_index("item_id")
    assert (t.loc["T1", "unit"], t.loc["T1", "unit_source"]) == ("명", "item_name")
    assert (t.loc["T2", "unit"], t.loc["T2", "unit_source"]) == ("단위 미상", "unknown")


def test_symbol_cells_stay_empty_and_totals_are_marked(tmp_path):
    key = _write(tmp_path, [_row(value_raw="x", value=None, value_kind="x_secret", dim1_code="1", dim1_label="남자"),
                            _row(value_raw="-", value=None, value_kind="dash", dim1_code="2", dim1_label="여자"),
                            _row()])
    t = fetch_table(key, data_dir=tmp_path).frame.set_index("dim1_label")
    assert t.loc["남자", "value_flag"] == "suppressed" and t.loc["여자", "value_flag"] == "missing"
    assert t.loc[["남자", "여자"], "value"].isna().all()  # never 0
    assert t["dim1_total"].to_dict() == {"남자": False, "여자": False, "계": True}


def test_text_values_are_parsed_without_inventing_numbers(tmp_path):
    # every row needs parsing (other_text): "560p" is provisional, free text stays empty
    key = _write(tmp_path, [_row(value_raw="560p", value=None, value_kind="other_text"),
                            _row(item_id="T2", value_raw="해당없음", value=None, value_kind="other_text")])
    t = fetch_table(key, data_dir=tmp_path).frame.set_index("item_id")
    assert (t.loc["T1", "value"], t.loc["T1", "value_flag"]) == (560.0, "provisional")
    assert pd.isna(t.loc["T2", "value"]) and t.loc["T2", "value_flag"] == "unparsed"


def test_large_table_needs_filters(tmp_path):
    key = _write(tmp_path, [_row(period=str(2000 + i), value_raw=str(i), value=float(i)) for i in range(10)])
    with pytest.raises(TableTooLarge):
        fetch_table(key, data_dir=tmp_path, row_limit=5)
    t = fetch_table(key, data_dir=tmp_path, row_limit=5, periods=["2003", "2004"])
    assert sorted(t.frame["period"]) == ["2003", "2004"]


def test_unknown_table_raises_lookup_error(tmp_path):
    _write(tmp_path, [_row()])
    with pytest.raises(LookupError):
        fetch_table("101_DT_NONE", data_dir=tmp_path)
