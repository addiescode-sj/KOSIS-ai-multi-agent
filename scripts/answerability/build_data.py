"""Answerability classifier (KOS-193 draft): build the Colab training data on the PC.

Inputs
    --pool-dir   a mix_ft_data.py folder (default: the Drive copy of mixft-v2/v2-strict): pool.jsonl + facts.json.
                 Question rows only (P1 older synthetic, P2 Claude questions; doc == "table").
    --tables     data/tables.jsonl (registry: real periods, items, class labels -> cards.py).
    --exclude    KOS-192 test set file(s) (dev + test). Only their table keys are read (required + .meta target_key);
                 question text is never read into the output or printed. Every row whose table or ok table is one of
                 them is dropped, and those tables get no card, so the trainer can never use them, not even as negatives.
Variants (code only, no model): for a question with exactly one explicit year y (no range / month words) that its gold
table holds,
    no_period    y -> a year no ok table holds (label 0 with the gold table). Sub-kinds, drawn by weight among the ones
                 that exist: sib (a year a family / name sibling holds: the "filter removed the topic table" shape),
                 gap (inside the gold range, e.g. a 5-year survey's middle year), after, before.
    period_twin  for that new year, a same-name / same-twin sibling that does hold it (label 1): the card must be read.
    period_ctrl  y -> another year the gold table holds (label 1): changing a year alone must not mean "no".
Outputs (--out, default data/lab/answerability/<build>): variants.jsonl (orig rows + variants), cards.json (key ->
card text, excluded tables left out), facts.json (copied from the pool dir), stats.json, and <out>.zip of the folder.

    python scripts\\answerability\\build_data.py --selftest
    python scripts\\answerability\\build_data.py --pool-dir "G:\\내 드라이브\\colab_project\\kosis-embedder-train\\data\\mixft-v2\\v2-strict" --build ans-v1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cards import CARD_FORMAT, card, load_records, years_of  # noqa: E402

YEAR = re.compile(r"(?<!\d)(19\d\d|20\d\d)(?!\d)")
# a range, a decade, a relative span or a sub-year period: the single-year swap would change more than the year
SKIP = re.compile(r"~|부터|까지|사이|이후|이전|동안|년대|이래|대비|증감|변화|추이|월|분기|반기")
SUB_WEIGHTS = {"sib": 0.4, "gap": 0.25, "after": 0.2, "before": 0.15}
YEAR_MIN, YEAR_MAX = 1950, 2040
QUESTION_TYPES = ("P1", "P2")


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def sha8(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def excluded_keys(paths: list[Path]) -> set[str]:
    """Table keys of the held-out sets: `required` (list of keys or of key lists) and the .meta.jsonl `target_key`."""
    out: set[str] = set()
    for p in paths:
        for x in read_jsonl(p):
            for s in x.get("required") or []:
                out |= {s} if isinstance(s, str) else {t for t in s if isinstance(t, str)}
        meta = p.with_name(p.name.replace(".jsonl", ".meta.jsonl"))
        if meta.is_file() and meta != p:
            for x in read_jsonl(meta):
                tk = x.get("target_key")
                out |= {tk} if isinstance(tk, str) else set(tk or [])
    return out


def single_year(q: str) -> int | None:
    """The one explicit year of a question, or None (no year, several years, or range / month wording)."""
    ys = set(YEAR.findall(q))
    if len(ys) != 1 or SKIP.search(q):
        return None
    return int(next(iter(ys)))


def swap_year(q: str, y: int, new: int) -> str:
    return YEAR.sub(lambda m: str(new) if int(m.group(1)) == y else m.group(1), q)


def same_table_kind(a: str, b: str, facts: dict) -> bool:
    """Same normalized name or same twin key (one table of another year / level written another way)."""
    fa, fb = facts.get(a), facts.get(b)
    if not fa or not fb or a == b:
        return False
    return fa["name"] == fb["name"] or (fa.get("twin") is not None and fa.get("twin") == fb.get("twin"))


def variants(row: dict, years: dict[str, list[int]], facts: dict, rng: random.Random) -> list[dict]:
    """no_period (+ period_twin) and period_ctrl rows for one question row (see the module text)."""
    y = single_year(row["q"])
    gold = row["table"]
    if y is None or gold not in years or y not in years[gold]:
        return []
    ok_years = set().union(*(years.get(k, []) for k in [gold, *row["ok"]]))
    g = years[gold]
    sibs = [k for k in dict.fromkeys([*row.get("name", []), *row.get("family", [])]) if k in years]
    pools = {
        "sib": sorted(set().union(*(years[k] for k in sibs)) - ok_years) if sibs else [],
        "gap": sorted(set(range(g[0], g[-1] + 1)) - ok_years),
        "after": [v for v in range(max(ok_years) + 1, max(ok_years) + 9) if v <= YEAR_MAX],
        "before": [v for v in range(min(ok_years) - 10, min(ok_years)) if v >= YEAR_MIN],
    }
    pools = {k: [v for v in vs if YEAR_MIN <= v <= YEAR_MAX] for k, vs in pools.items()}
    out = []
    subs = [k for k in SUB_WEIGHTS if pools[k]]
    if subs:
        sub = rng.choices(subs, weights=[SUB_WEIGHTS[k] for k in subs])[0]
        new = rng.choice(pools[sub])
        q2 = swap_year(row["q"], y, new)
        base = {"src": row["id"], "type": row["type"], "q": q2, "year_from": y, "year_to": new}
        out.append({**base, "id": f"{row['id']}~np", "kind": "no_period", "sub": sub, "table": gold, "label": 0})
        twins = [k for k in sibs if new in years[k] and same_table_kind(gold, k, facts)]
        if twins:
            out.append({**base, "id": f"{row['id']}~npt", "kind": "period_twin", "sub": sub, "table": rng.choice(twins),
                        "label": 1})
    others = [v for v in g if v != y]
    if others:
        new = rng.choice(others)
        out.append({"id": f"{row['id']}~pc", "src": row["id"], "type": row["type"], "kind": "period_ctrl", "sub": "",
                    "q": swap_year(row["q"], y, new), "table": gold, "label": 1, "year_from": y, "year_to": new})
    return out


def build(pool_dir: Path, tables: Path, exclude: list[Path], out: Path, seed: int) -> dict:
    recs = load_records(tables)
    facts_j = json.loads((pool_dir / "facts.json").read_text(encoding="utf-8"))
    facts = facts_j["tables"]
    held = excluded_keys(exclude)
    pool = [x for x in read_jsonl(pool_dir / "pool.jsonl") if x["type"] in QUESTION_TYPES and x.get("doc") != "desc"]
    keep = [x for x in pool if not ({x["table"], *x["ok"]} & held)]
    usable = {k for k in recs if k not in held}
    years = {k: years_of(recs[k].get("periods", [])) for k in usable}
    years = {k: v for k, v in years.items() if v}
    rng = random.Random(seed)
    rows, kinds = [], Counter()
    for x in keep:
        base = {k: x.get(k) for k in ("id", "type", "q", "table", "ok", "name", "family", "contrast")}
        base["name"] = [k for k in base.get("name") or [] if k in usable]
        base["family"] = [k for k in base.get("family") or [] if k in usable]
        rows.append({**base, "kind": "orig", "sub": "", "src": x["id"], "label": 1})
        kinds["orig"] += 1
        for v in variants(base, years, facts, rng):
            rows.append(v)
            kinds[f"{v['kind']}:{v['sub']}" if v["sub"] else v["kind"]] += 1
    out.mkdir(parents=True, exist_ok=True)
    with (out / "variants.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "cards.json").write_text(json.dumps({k: card(recs[k]) for k in sorted(usable)}, ensure_ascii=False),
                                    encoding="utf-8")
    shutil.copyfile(pool_dir / "facts.json", out / "facts.json")
    stats = {
        "card_format": CARD_FORMAT, "seed": seed, "pool_dir": str(pool_dir), "pool_sha8": sha8(pool_dir / "pool.jsonl"),
        "tables_sha8": sha8(tables), "excluded_tables": len(held), "excluded_tables_in_registry": len(held & set(recs)),
        "pool_question_rows": len(pool), "rows_dropped_excluded": len(pool) - len(keep), "rows_kept": len(keep),
        "tables_kept": len({x["table"] for x in keep}), "cards": len(usable), "kinds": dict(sorted(kinds.items())),
        "single_year_rows": sum(single_year(x["q"]) is not None for x in keep),
    }
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    zip_path = shutil.make_archive(str(out), "zip", root_dir=out.parent, base_dir=out.name)
    stats["zip"] = zip_path
    return stats


def selftest() -> None:
    import tempfile

    assert single_year("2020년 서울 인구") == 2020
    assert single_year("2015년부터 2020년까지 인구") is None and single_year("2020년 3월 출생아") is None
    assert single_year("서울 인구") is None and single_year("2019년과 2020년 인구") is None
    assert swap_year("2020년 인구, 2020 기준", 2020, 2003) == "2003년 인구, 2003 기준"
    facts = {"A": {"name": "성별 인구", "twin": "t1"}, "B": {"name": "성별 인구", "twin": "t1"},
             "C": {"name": "혼인 건수", "twin": "t2"}}
    years = {"A": [2000, 2005, 2010], "B": [2011, 2012], "C": [1990, 2003]}
    row = {"id": "S-1", "type": "P2", "q": "2005년 성별 인구는?", "table": "A", "ok": ["A"], "name": ["B"],
           "family": ["C"]}
    seen = Counter()
    for s in range(200):
        for v in variants(row, years, facts, random.Random(s)):
            seen[v["kind"]] += 1
            if v["kind"] == "no_period":
                assert v["year_to"] not in years["A"] and v["label"] == 0 and str(v["year_to"]) in v["q"]
                if v["sub"] == "gap":
                    assert 2000 < v["year_to"] < 2010
                if v["sub"] == "sib":
                    assert v["year_to"] in (1990, 2003, 2011, 2012)
            if v["kind"] == "period_twin":
                assert v["table"] == "B" and v["year_to"] in (2011, 2012) and v["label"] == 1
            if v["kind"] == "period_ctrl":
                assert v["year_to"] in (2000, 2010) and v["label"] == 1
    assert seen["no_period"] == 200 and seen["period_ctrl"] == 200 and seen["period_twin"] > 0, seen
    assert variants({**row, "q": "성별 인구"}, years, facts, random.Random(0)) == []
    assert variants({**row, "q": "1999년 성별 인구"}, years, facts, random.Random(0)) == []  # gold lacks the year

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        pd = td / "pool"
        pd.mkdir()
        rows = [{"id": "S-1", "type": "P2", "doc": "table", "q": "2005년 성별 인구는?", "table": "A", "ok": ["A"],
                 "name": ["B"], "family": ["C", "X"], "contrast": False},
                {"id": "S-2", "type": "P1", "doc": "table", "q": "혼인 건수", "table": "C", "ok": ["C"], "name": [],
                 "family": [], "contrast": False},
                {"id": "S-3", "type": "P1", "doc": "table", "q": "외국인 수", "table": "X", "ok": ["X"], "name": [],
                 "family": [], "contrast": False},
                {"id": "S-1~d", "type": "P5", "doc": "desc", "q": "x", "table": "A", "ok": ["A"]}]
        (pd / "pool.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        (pd / "facts.json").write_text(json.dumps({"tables": facts}), encoding="utf-8")
        recs = []
        for k, ys in {**years, "X": [2020]}.items():
            recs.append({"info": {"key": k, "table_name": facts.get(k, {"name": "외국인"})["name"]}, "prd_se": ["Y"],
                         "periods": [str(v) for v in ys], "items": ["계"], "classes": []})
        (td / "tables.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs), encoding="utf-8")
        held = td / "held.jsonl"
        held.write_text(json.dumps({"qid": "q1", "required": [["X"]]}), encoding="utf-8")
        held.with_name("held.meta.jsonl").write_text(json.dumps({"qid": "q1", "target_key": "X"}), encoding="utf-8")
        st = build(pd, td / "tables.jsonl", [held], td / "out" / "b1", 0)
        assert st["rows_kept"] == 2 and st["rows_dropped_excluded"] == 1 and st["cards"] == 3, st
        cards_j = json.loads((td / "out" / "b1" / "cards.json").read_text(encoding="utf-8"))
        assert "X" not in cards_j and "수록 연도: 2000, 2005, 2010" in cards_j["A"]
        out_rows = read_jsonl(td / "out" / "b1" / "variants.jsonl")
        assert all("X" not in r.get("family", []) for r in out_rows)
        assert {r["kind"] for r in out_rows} >= {"orig", "no_period", "period_ctrl"}
        assert Path(st["zip"]).is_file()
    print("selftest ok")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--pool-dir", default=r"G:\내 드라이브\colab_project\kosis-embedder-train\data\mixft-v2\v2-strict")
    p.add_argument("--tables", default="data/tables.jsonl")
    p.add_argument("--exclude", nargs="*", default=["eval/golden/kos192/retrieval_test_kos192.jsonl"],
                   help="held-out set files (their table keys are excluded)")
    p.add_argument("--build", default="ans-v1")
    p.add_argument("--out-root", default="data/lab/answerability")
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    if a.selftest:
        selftest()
        return
    missing = [x for x in [Path(a.pool_dir) / "pool.jsonl", Path(a.tables), *map(Path, a.exclude)] if not x.is_file()]
    if missing:
        raise SystemExit(f"missing input: {missing}")
    stats = build(Path(a.pool_dir), Path(a.tables), [Path(x) for x in a.exclude], Path(a.out_root) / a.build, a.seed)
    print(json.dumps(stats, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
