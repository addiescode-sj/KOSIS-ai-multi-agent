"""L0 metadata quality (T2-28, docs/research/langchain_eval_design_kosis.md §3): the table fields the filters decide on.

Fields are `kosis_rag.fields.TableFields` (T2-30, KOS-96), computed with the same functions the filters use, so the
score is about what the filter actually sees, not about a separate copy.

- fill rate: share of tables whose field has a value, over all tables and over the gold tables of the L1 set.
  regions has a third state, None: the table has no administrative class (a national table), which is a value.
- unmapped: measure — the name does not say what it counts (or says 기타); dimensions — breakdown names no question
  breakdown can match, i.e. a breakdown filter can never pick them.
- accuracy: a sample of tables labelled by the evaluation side, field by field (measure exact, dimensions as sets).
"""

from __future__ import annotations

from kosis_rag.fields import TableFields, table_fields
from kosis_rag.filters import has_dimension
from kosis_rag.query import DIMENSION_WORDS
from kosis_rag.schemas import TableRecord

FIELDS = tuple(f for f in TableFields.model_fields if f != "key")
CANONICAL_DIMS = sorted(set(DIMENSION_WORDS.values()))
CATCH_ALL = ("기타",)


def fields(record: TableRecord) -> dict:
    return table_fields(record).model_dump(exclude={"key"})


def filled(field: str, value) -> bool:
    return bool(value) or (field == "regions" and value is None)  # a national table's None counts as a value


def mapped_dimension(record: TableRecord, name: str) -> bool:
    return any(has_dimension(record, d, [name]) for d in CANONICAL_DIMS)


def score(records: dict[str, TableRecord], gold: set[str], sample: dict[str, dict] | None = None,
          loaded: set[str] | None = None) -> dict:
    """L0 summary; gold = table keys used as answers in the L1 set, sample = {key: labelled fields}, loaded = keys in
    the collection (None: not checked)."""
    values = {k: fields(r) for k, r in records.items()}
    out: dict = {"n_tables": len(records), "n_gold": len(gold & set(records))}
    for scope, keys in (("all", set(records)), ("gold", gold & set(records))):
        n = max(len(keys), 1)
        out[scope] = {
            "fill": {f: sum(filled(f, values[k][f]) for k in keys) / n for f in FIELDS},
            "regions_none": sum(values[k]["regions"] is None for k in keys) / n,
            "measure_unmapped": sum(not values[k]["measure"] or values[k]["measure"] in CATCH_ALL
                                    for k in keys) / n,
        }
        dims = [(k, d) for k in keys for d in values[k]["dimensions"]]
        out[scope]["dimension_names"] = len(dims)
        out[scope]["dimension_unmapped"] = (sum(not mapped_dimension(records[k], d) for k, d in dims) / len(dims)
                                            if dims else None)
    if sample:
        hits = {"measure": [], "dimensions": []}
        for key, label in sample.items():
            if key not in values:
                continue
            if "measure" in label:
                hits["measure"].append(int(label["measure"] == values[key]["measure"]))
            if "dimensions" in label:
                hits["dimensions"].append(int(set(label["dimensions"]) == set(values[key]["dimensions"])))
        out["accuracy"] = {f: (sum(v) / len(v) if v else None) for f, v in hits.items()}
        out["n_sample"] = len(sample)
    else:
        out["accuracy"] = None  # sample pending (§3.3)
    if loaded is not None:
        out["load"] = {"loaded": len(loaded), "missing": sorted(set(records) - loaded)[:20],
                       "extra": sorted(loaded - set(records))[:20], "match": loaded == set(records)}
    return out
