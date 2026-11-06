"""fetch_table(table_id) -> TidyTable: read one provided table into a tidy DataFrame (T3-17, docs/data-contract.md).

Rules (plans/T3-17.md):
- cell_key = tbl|prd_se|item|codes|period. prd_se is in the key: monthly and quarterly rows share period strings such
  as "197001" (1970-01 vs 1970 Q1).
- Exact duplicates (every source column equal, labels and value_raw included) keep one row. Rows that share a
  cell_key but differ in value_raw are flagged "ambiguous" with value None: the provided zip merged same-named items
  under different parents, and the Parquet file cannot tell which number belongs where. No number is picked.
- prd_se is kept as it is (F stays F, not Y).
- Units, symbol cells and totals reuse the phase 1 rules: loader.infer_unit, preprocessing.KIND_FLAGS / parse_value,
  judge.question.is_total. Symbol cells are never turned into 0 or an estimate.
- Only the needed columns and rows are read (pyarrow dataset filters). More rows than row_limit after the filters
  raises TableTooLarge before anything is loaded.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as ds
from kosis_ai import paths
from kosis_chat.data.loader import UNKNOWN_UNIT, infer_unit
from kosis_chat.data.preprocessing import KIND_FLAGS, parse_value
from kosis_chat.judge.question import is_total
from pydantic import BaseModel, ConfigDict

LEVELS = range(1, 9)
DIM_PARTS = ("name", "code", "label")
SOURCE_COLUMNS = ["prd_se", "period", "item_id", "item_name", "unit"] + [
    f"dim{i}_{p}" for i in LEVELS for p in DIM_PARTS] + ["value_raw", "value", "value_kind"]
# Rows read into the tidy frame after filters. 11/05 sample (plans/T3-17.md Result): tables up to 4.7M rows read with
# a peak of 2.5 GB in one process; 6.3M rows and up ran out of memory in the phase 1 loader.
ROW_LIMIT = 5_000_000
AMBIGUOUS = "ambiguous"


class TableTooLarge(ValueError):
    """The filtered table still has more rows than row_limit; pass items / periods / dims to narrow it."""

    def __init__(self, table_id: str, rows: int, limit: int) -> None:
        self.table_id, self.rows, self.limit = table_id, rows, limit
        super().__init__(f"{table_id}: {rows:,} rows after filters, limit {limit:,}; narrow by items, periods or dims")


class TidyTableMeta(BaseModel):
    key: str
    tbl_id: str
    table_name: str
    survey_name: str
    organization: str
    source_url: str
    prd_se: list[str]  # cycle codes in the rows read, unchanged (Y, F, M, Q, H, IR ...)
    file_sha256: str
    rows_in_file: int


class TidyTable(BaseModel):
    """meta + frame (columns in docs/data-contract.md) + issues (rule counts for this read)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    meta: TidyTableMeta
    frame: pd.DataFrame
    issues: dict[str, int]


def _catalog_row(data_dir: Path, table_id: str) -> dict:
    catalog = data_dir / "tables.csv"
    if not catalog.exists():
        raise LookupError(f"no tables.csv in {data_dir} (set {paths.ENV_VAR} to the folder that holds data/)")
    with catalog.open(encoding="utf-8-sig", newline="") as f:
        row = next((r for r in csv.DictReader(f) if r.get("key") == table_id), None)
    if row is None:
        raise LookupError(f"{table_id} is not in {catalog}")
    return row


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _filter(items, periods, prd_se, dims) -> pc.Expression | None:
    parts = []
    if items:
        parts.append(pc.field("item_id").isin(list(items)))
    if periods:
        parts.append(pc.field("period").isin([str(p) for p in periods]))
    if prd_se:
        parts.append(pc.field("prd_se").isin(list(prd_se)))
    for level, codes in (dims or {}).items():
        parts.append(pc.field(f"dim{int(level)}_code").isin([str(c) for c in codes]))
    if not parts:
        return None
    expr = parts[0]
    for p in parts[1:]:
        expr = expr & p
    return expr


def _flags(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """(value, value_flag) per row, as phase 1 preprocessing.clean_cell decides them."""
    flag = frame["value_kind"].map(KIND_FLAGS)
    value = frame["value"].where(flag == "ok")
    other = flag.isna()
    if other.any():  # other_text and unknown kinds: parse value_raw (e.g. "560p" -> provisional)
        parsed = frame.loc[other, "value_raw"].fillna("").map(parse_value)
        value.loc[other] = parsed.map(lambda p: p[0]).astype("float64")  # None -> NaN
        flag.loc[other] = parsed.map(lambda p: p[1])
    return value.astype("float64"), flag


def _units(frame: pd.DataFrame, levels: list[int]) -> tuple[pd.Series, pd.Series]:
    """(unit, unit_source) with loader.infer_unit, computed once per distinct (unit, item, labels)."""
    cols = ["unit", "item_name"] + [f"dim{i}_label" for i in levels]
    keys = frame[cols].fillna("").astype(str)
    uniq = keys.drop_duplicates()
    found = {tuple(r): infer_unit(r[0], r[1].strip(), [x.strip() for x in r[2:] if x])
             for r in uniq.itertuples(index=False, name=None)}
    pairs = [found[tuple(r)] for r in keys.itertuples(index=False, name=None)]
    return (pd.Series([p[0] for p in pairs], index=frame.index), pd.Series([p[1] for p in pairs], index=frame.index))


def fetch_table(table_id: str, *, items=None, periods=None, prd_se=None, dims: dict[int, list[str]] | None = None,
                data_dir: Path | None = None, row_limit: int = ROW_LIMIT) -> TidyTable:
    """Read table `table_id` (a tables.csv key such as "101_DT_1B8000F"), optionally narrowed by item ids, period
    strings, cycle codes and per-level category codes. Raises LookupError for an unknown table, TableTooLarge when
    the filtered rows exceed row_limit."""
    data_dir = Path(data_dir) if data_dir else paths.data_dir()
    row = _catalog_row(data_dir, table_id)
    path = data_dir / row["parquet"]
    if not path.exists():
        raise LookupError(f"{table_id}: {path} does not exist")
    dataset = ds.dataset(path, format="parquet")
    expr = _filter(items, periods, prd_se, dims)
    rows_in_file = dataset.count_rows()
    rows = dataset.count_rows(filter=expr) if expr is not None else rows_in_file
    if rows > row_limit:
        raise TableTooLarge(table_id, rows, row_limit)
    frame = dataset.to_table(columns=SOURCE_COLUMNS, filter=expr).to_pandas()

    levels = [i for i in LEVELS if frame[f"dim{i}_name"].notna().any()]
    frame = frame[["prd_se", "period", "item_id", "item_name", "unit"]
                  + [f"dim{i}_{p}" for i in levels for p in DIM_PARTS] + ["value_raw", "value", "value_kind"]]
    n_read = len(frame)
    frame = frame.drop_duplicates(ignore_index=True)  # exact duplicates: every source column equal
    exact_dropped = n_read - len(frame)

    codes = [frame[f"dim{i}_code"].fillna("").astype(str) for i in levels]
    code_part = codes[0].str.cat(codes[1:], sep="|") if codes else pd.Series("", index=frame.index)
    frame["cell_key"] = (row["tbl_id"] + "|" + frame["prd_se"].fillna("") + "|" + frame["item_id"].fillna("") + "|"
                         + code_part + "|" + frame["period"].astype(str))

    value, flag = _flags(frame)
    # After exact duplicates, a cell_key with several value_raw strings has no single number.
    pairs = frame[["cell_key", "value_raw"]].fillna({"value_raw": ""}).drop_duplicates()
    shared = frame["cell_key"].map(pairs["cell_key"].value_counts()) > 1
    same_value = frame.duplicated("cell_key", keep=False) & ~shared  # same number, other text (e.g. a label spelling)
    flag = flag.where(~shared, AMBIGUOUS)
    value = value.where(~shared)
    frame["value"], frame["value_flag"] = value, flag
    frame["unit"], frame["unit_source"] = _units(frame, levels)
    for i in levels:
        labels = frame[f"dim{i}_label"]
        totals = {lab: is_total(str(lab)) for lab in labels.dropna().unique()}
        frame[f"dim{i}_total"] = labels.map(totals).fillna(False).astype(bool)

    issues = {
        "rows_read": n_read,
        "exact_duplicates_dropped": exact_dropped,
        "ambiguous_cells": int(shared.sum()),
        "ambiguous_keys": int(frame.loc[shared, "cell_key"].nunique()),
        "same_value_key_rows": int(same_value.sum()),
        "unit_unknown_cells": int((frame["unit"] == UNKNOWN_UNIT).sum()),
        "symbol_cells": int(flag.isin(["missing", "suppressed", "flagged", "unparsed"]).sum()),
    }
    meta = TidyTableMeta(
        key=table_id, tbl_id=row["tbl_id"], table_name=row["table_name"], survey_name=row["survey_name"],
        organization=row["organization"], source_url=row["url"],
        prd_se=sorted(frame["prd_se"].dropna().unique().tolist()), file_sha256=_sha256(path),
        rows_in_file=rows_in_file)
    return TidyTable(meta=meta, frame=frame, issues=issues)
