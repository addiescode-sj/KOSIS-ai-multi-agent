"""L3/L4 golden items drawn from every survey in data/tables.jsonl (T2-31). Facts come from Parquet cells, by code.

    python -m evals.builders.diverse                  # template questions + calculation_diverse_draft.csv
    python -m evals.builders.diverse --rewrites evals/datasets/calculation_diverse_rewrites.jsonl

Tables are sampled per survey (seeded), annual cells only (prd_se Y/F, 4-digit period). Per table: value, compare
(two labels of one dim), trend (first vs latest year) and one refusal (missing_value on an X/-/* cell, no_period
before the first year, or no_item for an item the table lacks). A rewrite line {"qid", "question", "item_term"?}
replaces the template question only when `check_question` passes: the asked years, every non-total label (or a
region short name), the item name (or `item_term`, which must share two characters with it) and no other digits.
Output: calculation_golden_diverse.jsonl (+ .meta.json), next to the Phase 1 file that builders/phase1.py owns.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter
from itertools import zip_longest
from pathlib import Path

import pyarrow.parquet as pq

from kosis_ai.schemas import Cell, Fact

from evals.builders.phase1 import sha256
from evals.evaluators.calculation import _NUMBER, allowed_text_numbers
from evals.schemas import DATASETS, ROOT, TABLES, CalcItem, load_jsonl

OUT = DATASETS / "calculation_golden_diverse.jsonl"
DRAFT = DATASETS / "calculation_diverse_draft.csv"
REVIEW = DATASETS / "calculation_diverse_review_sample.csv"  # every 5th item, for a person to check the wording
TOTALS = {"계", "합계", "총계", "총합계", "전체", "소계", "전국", "총인구", "Total", "전규모"}
SYMBOLS = {"x_secret", "dash", "asterisk"}
ANNUAL = {"Y", "F", "IR"}  # IR (세종시 특별센서스) periods are plain years too
# Items no population table carries: a no_item question asks one of these with the table's own labels
FOREIGN_ITEMS = ["월평균 가구소득", "아파트 매매가격", "실업률", "1인당 국내총생산"]
REGION_SUFFIX = re.compile(r"(특별자치시|특별자치도|특별시|광역시|도)$")
SHORT = {"충청북도": "충북", "충청남도": "충남", "전라북도": "전북", "전북특별자치도": "전북", "전라남도": "전남",
         "경상북도": "경북", "경상남도": "경남", "강원특별자치도": "강원", "세종특별자치시": "세종"}


def is_total(label: str) -> bool:
    core = label.replace(" ", "")
    return core in TOTALS or core.endswith("계")  # 아시아주계, 가구_계, 주택-계: subtotals


_BATCHIM_DIGITS = set("013678")


def has_batchim(word: str) -> bool:
    word = re.sub(r"\s*\(.*?\)\s*$", "", word).strip() or word
    last = word[-1]
    if "가" <= last <= "힣":
        return (ord(last) - 0xAC00) % 28 != 0
    return last in _BATCHIM_DIGITS


def josa(word: str, pair: str) -> str:
    """word + 은/는, 와/과, 이/가 by its final consonant: josa('입국자', '은는') -> '입국자는'."""
    return word + (pair[0] if has_batchim(word) else pair[1])


def year_text(period: str) -> str:
    return f"{period}년"


def strata(rows: list[dict]) -> dict[str, list[dict]]:
    """Survey name; 인구총조사 (1,876 of 2,364 tables) is split by its menu section so 가구부문 is not crowded out."""
    out: dict[str, list[dict]] = {}
    for r in rows:
        info = r["info"]
        if info["coverage"] == "C_수집누락" or not (ANNUAL & set(r["prd_se"] or [])):
            continue
        name = info["survey_name"]
        if name == "인구총조사":
            parts = info["menu_path"].split(" > ")
            name += "/" + (parts[3] if len(parts) > 3 else "")
        out.setdefault(name, []).append(r)
    return out


def sample(rows: list[dict], per_stratum: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    picked = []
    for name, group in sorted(strata(rows).items()):
        n = per_stratum * 2 if name.startswith("인구총조사/") and len(group) > 100 else per_stratum
        picked += rng.sample(sorted(group, key=lambda r: r["info"]["key"]), min(n, len(group)))
    return picked


def load(path: Path):
    df = pq.read_table(path).to_pandas()
    df = df[df.prd_se.isin(list(ANNUAL)) & df.period.str.fullmatch(r"\d{4}")]
    # survey sample sizes (사례수) are not statistics a user asks for
    sample_size = df.item_name.str.contains("사례수", regex=False, na=False)
    for c in (c for c in df.columns if c.endswith("_label")):
        sample_size |= df[c].str.contains("사례수", regex=False, na=False)
    df = df[~sample_size]
    dims = [f"dim{i}" for i in range(1, 9) if df[f"dim{i}_code"].notna().any()]
    return df, dims


def cell(row) -> Cell:
    return Cell.from_row({k: (None if v != v else v) for k, v in row.items()})  # NaN -> None


def labels(c: Cell) -> dict[str, str]:
    return {d.name: d.label for d in c.dims}


def fact(fid: str, c: Cell, many_items: bool) -> Fact:
    dims = labels(c) | ({"항목": c.item_name} if many_items else {})
    if c.value is None:
        return Fact(id=fid, unit=c.unit_or_suffix(), period=c.period, dims=dims, source_ref=c.ref(), raw=c.value_raw)
    return Fact(id=fid, value=c.value, unit=c.unit_or_suffix(), period=c.period, dims=dims, source_ref=c.ref())


def decimals(*cells: Cell) -> int:
    return max((len(c.value_raw.split(".")[1]) if "." in c.value_raw else 0) for c in cells)


def subject(c: Cell, skip: str | None = None) -> str:
    """Non-total labels plus the item name: the words a question needs to pin the cell down."""
    words = [d.label for d in c.dims if not is_total(d.label) and d.name != skip]
    return " ".join([*words, c.item_name])


def simplest(frame, dims: list[str]):
    """Rows with the most total labels first: a cell a short question can name without ambiguity."""
    totals = sum(frame[f"{d}_label"].map(lambda v: isinstance(v, str) and is_total(v)).astype(int) for d in dims) if dims else 0
    return frame.assign(_t=totals).sort_values("_t", ascending=False, kind="stable")


def make_items(key: str, sha: str, df, dims: list[str], rng: random.Random, refusal_turn: int) -> list[CalcItem]:
    ok = df[df.value_kind == "ok"]
    if ok.empty:
        return []
    many = df.item_id.nunique() > 1
    series = ["item_id", *[f"{d}_code" for d in dims]]
    latest = ok.period.max()
    at = simplest(ok[ok.period == latest], dims)
    top = at[at._t == at._t.max()]
    base = cell(top.iloc[rng.randrange(len(top))].drop("_t"))
    unit = base.unit_or_suffix()
    items: list[CalcItem] = []

    def add(suffix: str, **kw) -> None:
        items.append(CalcItem(qid=f"D-{key}-{suffix}", table_key=key, table_sha256=sha, **kw))

    add("VAL", kind="answerable", type="value", question=f"{year_text(latest)} {josa(subject(base), '은는')}?",
        required_facts=[fact("f1", base, many)])

    # compare: same item and labels, one dim takes two different non-total labels
    for d in rng.sample(dims, len(dims)):
        same = at
        for other in series:
            if other != f"{d}_code":
                same = same[same[other] == getattr_code(base, other)]
        pool = same[~same[f"{d}_label"].map(is_total).astype(bool)].sort_values(f"{d}_code")
        codes = list(pool[f"{d}_code"])
        # siblings only: a mixed 특성별 dim codes its groups by prefix (A03xx 기업형태, A04xx 매출규모)
        pairs = [i for i in range(len(codes) - 1)
                 if len(codes[i]) == len(codes[i + 1]) and codes[i][:-2] == codes[i + 1][:-2]
                 and not (codes[i].endswith("0") and codes[i][:-1] == codes[i + 1][:-1])  # 수원시 41110 vs 장안구 41111
                 # T3-46: one dim can mix units (구성비 % next to 평균 수입액 백만원), a difference needs one unit
                 and cell(pool.iloc[i].drop("_t")).unit_or_suffix() == cell(pool.iloc[i + 1].drop("_t")).unit_or_suffix()]
        if pairs:
            i = rng.choice(pairs)
            a, b = cell(pool.iloc[i].drop("_t")), cell(pool.iloc[i + 1].drop("_t"))
            dname = a.dims[dims.index(d)].name
            la, lb = labels(a)[dname], labels(b)[dname]
            digits = decimals(a, b)
            add("CMP", kind="answerable", type="compare",
                question=f"{year_text(latest)} {subject(a, skip=dname)}: {josa(la, '과와')} {lb}의 차이는?",
                required_facts=[fact("f1", a, many), fact("f2", b, many),
                                Fact(id="f3", value=round(a.value - b.value, digits),
                                     unit="%p" if a.unit_or_suffix() == "%" else a.unit_or_suffix(), derivation="f1 - f2", round=digits)])
            break

    # trend: the base series, first year with a value vs latest
    mask = True
    for col in series:
        mask = mask & (ok[col] == getattr_code(base, col))
    years = ok[mask].sort_values("period")
    if len(years) >= 2 and years.iloc[0].period != latest:
        first = cell(years.iloc[0])
        if unit == "%":  # a change of a rate is a difference in %p, not a growth rate
            f3 = Fact(id="f3", value=round(base.value - first.value, decimals(first, base)), unit="%p",
                      derivation="f2 - f1", round=decimals(first, base))
        elif first.value:
            f3 = Fact(id="f3", value=round((base.value / first.value - 1) * 100, 1), unit="%",
                      derivation="(f2 / f1 - 1) * 100", round=1)
        else:
            f3 = None
        if f3:
            add("TRD", kind="answerable", type="trend",
                question=f"{year_text(first.period)}부터 {year_text(latest)}까지 {josa(subject(base), '은는')} 얼마나 변했어?",
                required_facts=[fact("f1", first, many), fact("f2", base, many), f3])

    # one refusal per table, rotating through the reasons the table can support
    symbol = simplest(df[df.value_kind.isin(SYMBOLS)], dims)
    first_year = int(ok.period.min())
    choices = [r for r in ("missing_value", "no_period", "no_item") if r != "missing_value" or not symbol.empty]
    reason = choices[refusal_turn % len(choices)]
    if reason == "missing_value":
        c = cell(symbol.iloc[0].drop("_t"))
        add("REF", kind="not_answerable", type="value", question=f"{year_text(c.period)} {josa(subject(c), '은는')}?",
            aux_facts=[fact("a1", c, many)], allowed_reasons=["missing_value"], eda_traps=["x_symbol"])
    elif reason == "no_period":
        asked = str(first_year - 3)
        first = cell(years.iloc[0]) if len(years) else base
        add("REF", kind="not_answerable", type="value", question=f"{year_text(asked)} {josa(subject(base), '은는')}?",
            aux_facts=[fact("a1", first, many)], allowed_reasons=["no_period"])
    else:
        wanted = next(i for i in FOREIGN_ITEMS if i not in set(df.item_name))
        words = " ".join(d.label for d in base.dims if not is_total(d.label))
        add("REF", kind="not_answerable", type="value", question=f"{year_text(latest)} {words} {josa(wanted, '은는')}?".replace("  ", " "),
            allowed_reasons=["no_item", "out_of_scope"])
    return items


def getattr_code(c: Cell, column: str) -> str:
    if column == "item_id":
        return c.item_id
    return c.dims[int(column[3]) - 1].code  # dimN_code; dims are filled from dim1 up without gaps


def wanted_words(item: CalcItem) -> tuple[set[str], list[str], str | None]:
    """Years, non-total labels and item name a question for this item has to keep."""
    facts = [f for f in item.required_facts + item.aux_facts if f.source_ref]
    years = {m for m in re.findall(r"(\d{4})년", item.question)}
    labels_: list[str] = []
    item_name = None
    for f in facts:
        for name, label in (f.dims or {}).items():
            if name == "항목":
                item_name = label
            # T3-46: only grand totals may be left out; a subtotal (아시아주계, 화장실_재래식_계) or a word ending in 계
            # (자녀 승계) names a different cell than the total, so the question has to say it
            elif label.replace(" ", "") not in TOTALS and label not in labels_:
                labels_.append(label)
    if item.kind == "not_answerable" and "no_item" in item.allowed_reasons:
        item_name = next((i for i in FOREIGN_ITEMS if i in item.question), None)
    return years, labels_, item_name


def squash(text: str) -> str:
    return re.sub(r"[\s·,()\-]", "", text)


def label_ok(label: str, question: str) -> bool:
    q = squash(question)
    core = squash(re.sub(r"\(.*?\)", "", label)) or squash(label)
    short = SHORT.get(label) or REGION_SUFFIX.sub("", label)
    return core in q or (len(short) >= 2 and squash(short) in q)  # q is squashed, so short must be too


def check_question(item: CalcItem, template: str, question: str, item_term: str | None, item_name: str | None,
                   table_name: str = "") -> list[str]:
    """Reasons a rewritten question loses what the template pins down; empty means accepted."""
    problems = []
    years, labels_, _ = wanted_words(item.model_copy(update={"question": template}))
    for y in years:
        if y not in question:
            problems.append(f"year {y} missing")
    for label in labels_:
        if not label_ok(label, question):
            problems.append(f"label {label!r} missing")
    if item_name:
        core = squash(re.sub(r"\(.*?\)", "", item_name))
        if core not in squash(question):
            shares = item_term and any(core[i:i + 2] in squash(item_term) for i in range(len(core) - 1))
            if not (item_term and squash(item_term) in squash(question) and shares):
                problems.append(f"item {item_name!r} missing (item_term {item_term!r})")
    # template digits come from cell labels and item names (code), e.g. '12세 이상' or a no_item question's labels
    # T3-46: a 구성비 cell (%) read as a count ("개인사업체는 몇 곳이야?" for 53.4%) asks for a number the table lacks
    if item.required_facts and item.required_facts[0].unit == "%" and COUNT_WORDS.search(question):
        problems.append("asks a count but the cell is %")
    allowed = allowed_text_numbers(item.required_facts + item.aux_facts) | years | set(_NUMBER.findall(template))
    allowed |= set(_NUMBER.findall(table_name))  # T3-46: '1인가구', '12세 이상' from the table's own name
    stray = [n for n in _NUMBER.findall(question) if n not in allowed and n.replace(",", "") not in allowed]
    if stray:
        problems.append(f"stray numbers {stray}")
    return problems


COUNT_WORDS = re.compile(r"몇\s*(?:곳|명|개|가구|건|호|세대|사람)|수는\s*몇")
TYPES = ("value", "compare", "trend")


def quotas(counts: dict[str, int], total: int, per_table: int) -> dict[str, int]:
    """Water-fill: every survey gets the same number of questions, capped at per_table questions per table it has."""
    left, out = dict(counts), {}
    while left:
        share = total / len(left)
        capped = {k: n * per_table for k, n in left.items() if n * per_table <= share}
        if not capped:
            base, extra = divmod(total, len(left))
            out |= {k: base + (i < extra) for i, k in enumerate(sorted(left))}
            break
        for k, cap in capped.items():
            out[k], total = cap, total - cap
            del left[k]
    return out


def balanced(rows: list[dict], total: int, seed: int, values: Path, per_table: int = 2) -> tuple[list[CalcItem], dict[str, str | None]]:
    """T3-46: `total` answerable questions spread evenly over surveys and, inside a survey, over its menu sections.

    One question per table; a survey with fewer tables than its share asks a second question type on the same
    tables. Smallest surveys go first, so a table that yields nothing leaves its share to the bigger surveys."""
    rng = random.Random(seed)
    by_survey: dict[str, list[dict]] = {}
    for r in rows:
        if r["info"]["coverage"] != "C_수집누락" and ANNUAL & set(r["prd_se"] or []) and (values / r["info"]["parquet"]).exists():
            by_survey.setdefault(r["info"]["survey_name"], []).append(r)
    out: list[CalcItem] = []
    names: dict[str, str | None] = {}
    turn = 0
    order = sorted(by_survey, key=lambda k: (len(by_survey[k]), k))
    for n, name in enumerate(order):
        rest = {k: len(by_survey[k]) for k in order[n:]}
        need = quotas(rest, total - len(out), per_table)[name]
        groups: dict[str, list[dict]] = {}
        for r in sorted(by_survey[name], key=lambda r: r["info"]["key"]):
            groups.setdefault(r["info"]["menu_path"].rsplit(" > ", 1)[0], []).append(r)
        lanes = [rng.sample(g, len(g)) for _, g in sorted(groups.items())]
        rng.shuffle(lanes)
        tables = [r for tier in zip_longest(*lanes) for r in tier if r]  # round-robin over menu sections
        picked: list[CalcItem] = []
        made: list[tuple[dict[str, CalcItem], str]] = []
        for r in tables:
            if len(picked) >= need:
                break
            path = values / r["info"]["parquet"]
            df, dims = load(path)
            items = make_items(r["info"]["key"], sha256(path), df, dims, random.Random(f"{seed}-{r['info']['key']}"), 0)
            by_type = {i.type: i for i in items if i.kind == "answerable"}
            for i in items:
                names[i.qid] = next((f.dims["항목"] for f in i.required_facts if f.dims and "항목" in f.dims), None) \
                    or (df.item_name.iloc[0] if df.item_id.nunique() == 1 else None)
            if not by_type:
                continue
            want = TYPES[turn % len(TYPES)]
            turn += 1
            first = by_type.get(want) or next(by_type[t] for t in TYPES if t in by_type)
            picked.append(first)
            made.append((by_type, first.type))
        for by_type, used in made:  # second question type on the same tables, only when tables ran out
            if len(picked) >= need:
                break
            other = [t for t in TYPES if t in by_type and t != used]
            if other:
                picked.append(by_type[other[0]])
        out += picked
    return out, names


def item_name_of(item: CalcItem, df_items: dict[str, str]) -> str | None:
    _, _, name = wanted_words(item)
    return name or df_items.get(item.qid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sampled tables -> evals/datasets/calculation_golden_diverse.jsonl")
    parser.add_argument("--tables", type=Path, default=TABLES)
    parser.add_argument("--values", type=Path, default=ROOT / "data")
    parser.add_argument("--per-stratum", type=int, default=4)
    parser.add_argument("--seed", type=int, default=31)
    parser.add_argument("--rewrites", type=Path, help="jsonl of {qid, question, item_term?} written from the draft")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--extend", type=Path, help="T3-46: keep this golden file's items as they are and add tables it lacks")
    parser.add_argument("--total", type=int, help="T3-46: this many answerable questions balanced over surveys and tables")
    args = parser.parse_args(argv)

    rows = [json.loads(line) for line in args.tables.read_text(encoding="utf-8").splitlines() if line.strip()]
    meta = {r["info"]["key"]: r["info"] for r in rows}
    base, dropped = [], []
    if args.extend:
        # T3-46: an item whose table has no Parquet in data/values cannot be answered by phase 1, so it leaves the set
        for it in load_jsonl(args.extend, CalcItem):
            (base if (args.values / meta[it.table_key]["parquet"]).exists() else dropped).append(it)
        used = {it.table_key for it in base} | {it.table_key for it in dropped}
        rows = [r for r in rows if r["info"]["key"] not in used and (args.values / r["info"]["parquet"]).exists()]
    offset = len({it.table_key for it in base})  # refusal reasons keep rotating after the base tables
    items, item_names = [], {}
    if args.total:
        items, item_names = balanced(rows, args.total, args.seed, args.values)
    for n, r in enumerate([] if args.total else sample(rows, args.per_stratum, args.seed)):
        path = args.values / r["info"]["parquet"]
        df, dims = load(path)
        made = make_items(r["info"]["key"], sha256(path), df, dims, random.Random(f"{args.seed}-{r['info']['key']}"), offset + n)
        for it in made:
            item_names[it.qid] = next((f.dims.get("항목") for f in it.required_facts + it.aux_facts if f.dims and "항목" in f.dims), None) \
                or (df.item_name.iloc[0] if df.item_id.nunique() == 1 else None)
        items += made

    rewrites = {}
    if args.rewrites:
        rewrites = {(d := json.loads(line))["qid"]: d for line in args.rewrites.read_text(encoding="utf-8").splitlines() if line.strip()}
    out, kept, rejected = [], 0, []
    for it in items:
        name = item_name_of(it, item_names)
        rw = rewrites.get(it.qid)
        if rw:
            problems = check_question(it, it.question, rw["question"], rw.get("item_term"), name, meta[it.table_key]["table_name"])
            if problems:
                rejected.append(f"{it.qid}: {'; '.join(problems)}")
            else:
                it = it.model_copy(update={"question": rw["question"]})
                kept += 1
        out.append(it)
    out = base + out

    args.out.write_text("".join(i.model_dump_json(exclude_defaults=True) + "\n" for i in out), encoding="utf-8")
    new = out[len(base):]  # base items were drafted and reviewed with the file they come from
    drafts = (DRAFT, REVIEW) if args.out == OUT else (args.out.with_suffix(".draft.csv"), args.out.with_suffix(".review.csv"))
    for path, chosen in zip(drafts, (new, new[::5])):
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["qid", "kind", "survey", "table_name", "item_name", "question", "facts", "ok", "note"])
            for it in chosen:
                facts = "; ".join(f"{x.id}={x.value if x.value is not None else x.raw} {x.unit or ''} {x.period or ''} {x.dims or ''}"
                                  for x in it.required_facts + it.aux_facts)
                w.writerow([it.qid, it.kind, meta[it.table_key]["survey_name"], meta[it.table_key]["table_name"],
                            item_names.get(it.qid) or "", it.question, facts])
    tables = Counter(i.table_key for i in out)
    surveys = Counter(meta[k]["survey_name"] for k in tables)
    info = {"items": len(out), "tables": len(tables), "surveys": len(surveys), "max_table_share": round(max(tables.values()) / len(out), 3),
            "kinds": Counter(f"{i.kind}/{i.type}" for i in out), "rewritten": kept, "rejected": rejected,
            "seed": args.seed, "per_stratum": args.per_stratum, "total": args.total,
            "per_survey": Counter(meta[i.table_key]["survey_name"] for i in out),
            **({"extends": {"file": args.extend.name, "sha256": sha256(args.extend), "kept": len(base),
                            "dropped_no_parquet": sorted({i.table_key for i in dropped}), "dropped_items": len(dropped)}} if args.extend else {}), "tables.jsonl": sha256(args.tables), "sha256": sha256(args.out)}
    args.out.with_suffix(".meta.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in info.items() if k != "rejected"}, ensure_ascii=False))
    for line in rejected:
        print("rejected", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
