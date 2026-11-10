"""fetch_table(table_id) -> TidyTable: 제공된 표 하나를 tidy DataFrame으로 읽는다 (T3-17, docs/data-contract.md).

T3-23에서 src/kosis_data/fetch.py → src/kosis_agent/services/parquet_repository.py로 옮겼다(연구 문서 6장의
services/parquet_repository.py 자리). 코드는 바꾸지 않았다.

규칙 (plans/T3-17.md):
- cell_key = tbl|prd_se|item|codes|period. prd_se를 키에 넣는다. 월별과 분기별 행이 "197001" 같은 같은 기간
  문자열을 쓰기 때문이다(1970-01 vs 1970년 1분기).
- 완전 중복(라벨·value_raw를 포함해 모든 원본 열이 같음)은 한 행만 남긴다. cell_key는 같은데 value_raw가 다른
  행은 "ambiguous"로 표시하고 value를 None으로 둔다. 제공 zip이 다른 상위 항목 아래의 같은 이름 항목을 합쳐서,
  Parquet만으로는 어느 숫자가 어디 것인지 알 수 없기 때문이다. 숫자를 임의로 고르지 않는다.
- prd_se는 그대로 둔다(F는 Y로 바꾸지 않고 F).
- 단위·기호 셀·합계는 실전 1 규칙을 재사용한다: loader.infer_unit, preprocessing.KIND_FLAGS / parse_value,
  judge.question.is_total. 기호 셀을 0이나 추정값으로 바꾸지 않는다.
- 필요한 열과 행만 읽는다(pyarrow dataset 필터). 필터 뒤에도 row_limit보다 행이 많으면, 읽기 전에 TableTooLarge를 낸다.
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
# 필터 뒤 tidy frame에 읽어 들이는 최대 행 수. 11/05 표본(plans/T3-17.md Result): 470만 행까지는 한 프로세스에서
# 최대 2.5 GB로 읽혔고, 630만 행 이상은 실전 1 loader에서 메모리가 부족했다.
ROW_LIMIT = 5_000_000
AMBIGUOUS = "ambiguous"


class TableTooLarge(ValueError):
    """필터를 적용해도 row_limit보다 행이 많다. items / periods / dims로 더 좁혀서 다시 부른다."""

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
    prd_se: list[str]  # 읽은 행의 주기 코드, 바꾸지 않음 (Y, F, M, Q, H, IR ...)
    file_sha256: str
    rows_in_file: int


class TidyTable(BaseModel):
    """meta + frame (열 목록은 docs/data-contract.md) + issues (이번 읽기에서 규칙별로 센 수)."""

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
    """행마다 (value, value_flag). 실전 1 preprocessing.clean_cell과 같은 규칙으로 정한다."""
    flag = frame["value_kind"].map(KIND_FLAGS)
    value = frame["value"].where(flag == "ok")
    other = flag.isna()
    if other.any():  # other_text와 모르는 종류: value_raw를 파싱한다 (예: "560p" -> 잠정치)
        parsed = frame.loc[other, "value_raw"].fillna("").map(parse_value)
        value.loc[other] = parsed.map(lambda p: p[0]).astype("float64")  # None -> NaN
        flag.loc[other] = parsed.map(lambda p: p[1])
    return value.astype("float64"), flag


def _units(frame: pd.DataFrame, levels: list[int]) -> tuple[pd.Series, pd.Series]:
    """loader.infer_unit으로 (unit, unit_source)를 구한다. 서로 다른 (unit, item, labels) 조합마다 한 번만 계산한다."""
    cols = ["unit", "item_name"] + [f"dim{i}_label" for i in levels]
    keys = frame[cols].fillna("").astype(str)
    uniq = keys.drop_duplicates()
    found = {tuple(r): infer_unit(r[0], r[1].strip(), [x.strip() for x in r[2:] if x])
             for r in uniq.itertuples(index=False, name=None)}
    pairs = [found[tuple(r)] for r in keys.itertuples(index=False, name=None)]
    return (pd.Series([p[0] for p in pairs], index=frame.index), pd.Series([p[1] for p in pairs], index=frame.index))


def fetch_table(table_id: str, *, items=None, periods=None, prd_se=None, dims: dict[int, list[str]] | None = None,
                data_dir: Path | None = None, row_limit: int = ROW_LIMIT) -> TidyTable:
    """표 `table_id`(tables.csv 키, 예: "101_DT_1B8000F")를 읽는다. 항목 ID·기간 문자열·주기 코드·단계별 분류 코드로
    좁힐 수 있다. 모르는 표면 LookupError, 필터 뒤 행이 row_limit을 넘으면 TableTooLarge를 낸다."""
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
    frame = frame.drop_duplicates(ignore_index=True)  # 완전 중복: 모든 원본 열이 같음
    exact_dropped = n_read - len(frame)

    codes = [frame[f"dim{i}_code"].fillna("").astype(str) for i in levels]
    code_part = codes[0].str.cat(codes[1:], sep="|") if codes else pd.Series("", index=frame.index)
    frame["cell_key"] = (row["tbl_id"] + "|" + frame["prd_se"].fillna("") + "|" + frame["item_id"].fillna("") + "|"
                         + code_part + "|" + frame["period"].astype(str))

    value, flag = _flags(frame)
    # 완전 중복을 지운 뒤에도 cell_key 하나에 value_raw가 여러 개면, 그 셀에는 하나로 정할 숫자가 없다.
    pairs = frame[["cell_key", "value_raw"]].fillna({"value_raw": ""}).drop_duplicates()
    shared = frame["cell_key"].map(pairs["cell_key"].value_counts()) > 1
    same_value = frame.duplicated("cell_key", keep=False) & ~shared  # 숫자는 같고 다른 텍스트만 다름 (예: 라벨 철자)
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
