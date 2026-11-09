"""Golden set v2 for L1-L4 (T2-32): one question per sampled table, the same qid in every layer.

    python -m evals.builders.golden_v2 draft                    # tables, facts, template questions -> golden_v2_draft.csv
    (write golden_v2_texts.jsonl: one line per qid with the question, `terms` {label or item: phrase in the question},
L2 measures/dimensions, plus extra items)
    python -m evals.builders.golden_v2 pool                     # check texts, export retrieval_pool_v2.csv to judge
    (fill `slot` in retrieval_pool_v2.csv for every other table that answers the question, `auto`=claude on judged rows)
    python -m evals.builders.golden_v2 finish                   # -> retrieval/query/calculation_golden_v2.jsonl

Tables: every 인구총조사 menu section (parts 3-4 of menu_path) gives one table, every other survey up to five
(seeded), so no survey or table dominates. Facts and L3 refusals come from evals.builders.diverse (Parquet cells,
by code). L2 period, years and regions come from the facts; cycle only when the question names it; measures and
dimensions are the writer's labels (T2-28 vocabulary); a text line may override a code field, e.g. regions
for a 시도 that only the table name holds. Pool candidates that lack a fact's year or a non-total
label are ruled out by code (`auto` column) before anyone judges the rest. Extra items (multi_table, ambiguous,
no_answer) are written by hand in the texts file; a no_answer item lists `absent` terms that no table's
search_text may hold together (with `year` in its periods when given), checked here.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter
from pathlib import Path

from kosis_rag.shared.vocab import SIDO, SIDO_ORDER

from evals.builders import diverse
from evals.builders.phase1 import sha256
from evals.builders.pool import COLUMNS, POOL_K, apply_labels, assign_splits, pool_rows
from evals.schemas import DATASETS, ROOT, TABLES, CalcItem, QueryItem, RetrievalItem, check_items

WORK = DATASETS / "golden_v2_work.jsonl"
DRAFT = DATASETS / "golden_v2_draft.csv"
TEXTS = DATASETS / "golden_v2_texts.jsonl"
POOL = DATASETS / "retrieval_pool_v2.csv"
OUT_L1 = DATASETS / "retrieval_golden_v2.jsonl"
OUT_L2 = DATASETS / "query_golden_v2.jsonl"
OUT_L3 = DATASETS / "calculation_golden_v2.jsonl"
REVIEW = DATASETS / "golden_v2_review_sample.csv"
PER_SURVEY = 5
# qtype per table in turn, with the L3 item its question is built from (fallback: VAL)
ROTATION = [("exact_name", "VAL"), ("concept", "VAL"), ("multi_condition", "CMP"), ("period_cycle", "TRD"), ("sibling", "VAL")]
OFFICIAL = {name: name for name in SIDO_ORDER} | {alias: name for name, aliases in SIDO.items() for alias in aliases}
L2_CODE_FIELDS = ("period_start", "period_end", "years", "regions", "cycle")
CYCLE_WORDS = {"Y": ("연도별", "매년", "해마다"), "M": ("월별",), "Q": ("분기별",)}


def stratum(info: dict) -> str:
    if info["survey_name"] == "인구총조사":
        return "인구총조사/" + " > ".join(info["menu_path"].split(" > ")[3:5])
    return info["survey_name"]


def sample(rows: list[dict], seed: int) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r["info"]["coverage"] != "C_수집누락" and diverse.ANNUAL & set(r["prd_se"] or []):
            groups.setdefault(stratum(r["info"]), []).append(r)
    rng = random.Random(seed)
    picked = []
    for name, group in sorted(groups.items()):
        n = 1 if name.startswith("인구총조사/") else PER_SURVEY
        picked += rng.sample(sorted(group, key=lambda r: r["info"]["key"]), min(n, len(group)))
    return picked


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list) -> None:
    path.write_text("".join((r.model_dump_json(exclude_defaults=True) if hasattr(r, "model_dump_json")
                             else json.dumps(r, ensure_ascii=False)) + "\n" for r in rows), encoding="utf-8")


def draft(args) -> int:
    rows = read_jsonl(args.tables)
    work = []
    for n, r in enumerate(sample(rows, args.seed)):
        info = r["info"]
        path = args.values / info["parquet"]
        df, dims = diverse.load(path)
        made = {i.qid.rsplit("-", 1)[1]: i for i in
                diverse.make_items(info["key"], sha256(path), df, dims, random.Random(f"{args.seed}-{info['key']}"), n)}
        if "VAL" not in made:
            continue
        if r["unit"]:  # the cell has no unit column: take the table's own unit (registry), never a guess
            for it in made.values():
                for f in it.required_facts + it.aux_facts:
                    if f.source_ref and not f.unit:
                        f.unit = r["unit"]
        qtype, want = ROTATION[n % len(ROTATION)]
        rep = made.get(want, made["VAL"])
        single = df.item_name.iloc[0] if df.item_id.nunique() == 1 else None
        base = {"survey": info["survey_name"], "table_name": info["table_name"], "single_item": single}
        work.append({**base, "qid": f"V2-{info['key']}", "qtype": qtype, "calc": rep.model_copy(update={"qid": f"V2-{info['key']}"}).model_dump(mode="json", exclude_defaults=True)})
        if n % 5 == 2 and "REF" in made:  # L3-only refusal on the same table, about a fifth of the tables
            work.append({**base, "qid": f"V2-{info['key']}-REF", "qtype": None, "calc": made["REF"].model_copy(update={"qid": f"V2-{info['key']}-REF"}).model_dump(mode="json", exclude_defaults=True)})
    write_jsonl(WORK, work)
    with DRAFT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["qid", "qtype", "l3", "survey", "table_name", "item_name", "template", "facts"])
        for x in work:
            c = CalcItem.model_validate(x["calc"])
            facts = "; ".join(f"{f.id}={f.value if f.value is not None else f.raw} {f.period or ''} {f.dims or ''}" for f in c.required_facts + c.aux_facts)
            w.writerow([x["qid"], x["qtype"] or "", f"{c.kind}/{c.type}", x["survey"], x["table_name"], item_name(c, x), c.question, facts])
    tables = Counter(x["calc"]["table_key"] for x in work)
    print(f"{len(work)} items over {len(tables)} tables, {len({x['survey'] for x in work})} surveys -> {DRAFT}")
    return 0


def item_name(calc: CalcItem, work_row: dict) -> str | None:
    _, _, name = diverse.wanted_words(calc)
    return name or work_row.get("single_item")


def l2_from_facts(calc: CalcItem, question: str) -> dict:
    """period_*, years, regions and cycle a parser must read from this question, taken from the facts."""
    facts = [f for f in calc.required_facts + calc.aux_facts if f.source_ref]
    asked = re.findall(r"(\d{4})년(?!생)", question)
    periods = sorted({f.period for f in facts}) if facts else []
    if calc.kind == "not_answerable" and asked:  # no_period asks a year the table lacks
        periods = [asked[0]]
    trend = calc.type == "trend" and len(periods) == 2
    regions = sorted({OFFICIAL[label] for f in facts for label in (f.dims or {}).values() if label in OFFICIAL},
                     key=SIDO_ORDER.index)
    cycle = next((c for c, words in CYCLE_WORDS.items() if any(w in question for w in words)), None)
    return {"period_start": periods[0] if periods else None, "period_end": periods[-1] if periods else None,
            "years": [] if trend else [int(p) for p in periods], "regions": regions, "cycle": cycle}


def label_ok_in_table(label: str, meta: dict) -> bool:
    core = diverse.squash(re.sub(r"\(.*?\)", "", label)) or diverse.squash(label)
    names = [*meta["items"], *(lab for c in meta["classes"] for lab in c["labels"])]
    return any(core in diverse.squash(re.sub(r"\(.*?\)", "", n)) for n in names)


def rule_out(calc: CalcItem, meta: dict) -> str:
    """'' when the table could hold the answer's cells, else why not: a fact's year or a non-total label is missing."""
    for f in calc.required_facts:
        if f.period and f.period not in meta["periods"]:
            return f"rule:no_period {f.period}"
        for name, label in (f.dims or {}).items():
            if not diverse.is_total(label) and not label_ok_in_table(label, meta):
                return f"rule:no_label {label}"
    return ""


def absent_ok(item: dict, metas: dict) -> list[str]:
    """Tables whose search_text holds every `absent` term (and `year` in periods): must be none for no_answer."""
    terms, year = item.get("absent") or [], item.get("year")
    if not terms:
        return ["no `absent` terms given"]
    return [k for k, m in metas.items() if all(t in m["search_text"] for t in terms) and (not year or year in m["periods"])]


# Everyday words for official terms (T2-33): a question may say the user word instead of the label, when no other
# label of the same dimension fits the phrase as well. Reviewed as a fixed list, never written per question.
USER_WORDS = {
    "출생": ("태어난", "태어나", "아이", "아기", "신생아"), "출산": ("아이", "낳"), "혼인": ("결혼",), "이혼": ("헤어진", "갈라선"),
    "사망": ("돌아가신", "숨진", "죽은"), "가구": ("집", "세대"), "인구": ("사람", "주민"), "남자": ("남성",), "여자": ("여성",),
    "외국인": ("외국 사람", "외국에서 온"), "전입": ("이사 온", "이사와", "옮겨 온"), "전출": ("이사 간", "떠난", "빠져나간"),
    "이동": ("이사", "옮긴"), "입국": ("들어온", "들어오"), "출국": ("나간", "떠난"), "유학생": ("유학",),
    "근로자": ("일하는", "노동자"), "통근": ("출퇴근", "출근", "일하러"), "통학": ("학교 다니", "등교", "학교에 다니"),
    "취업": ("일하는", "일을 하는", "직장"), "유업자": ("일하는", "직업이 있는", "직장"), "실업": ("일자리가 없", "실직"),
    "미혼": ("결혼 안 한", "결혼하지 않은", "결혼을 안 한"), "유배우": ("배우자가 있는", "배우자 있는"),
    "사별": ("배우자를 잃은", "먼저 떠나보낸"), "주택": ("집",), "귀화": ("국적을 취득", "국적을 얻은", "국적 취득"),
    "자녀": ("아이", "아들딸", "자식"), "고령자": ("노인", "어르신"), "고령": ("노인", "어르신"), "매출": ("팔아", "판매"),
    "수출": ("해외에 판", "수출"), "비율": ("비중", "몇 퍼센트", "몇 %"), "이수": ("마친", "수료"), "추계": ("전망", "예상"),
    "체류": ("머무는", "머물"), "종사자": ("일하는 사람", "직원"), "부인": ("기혼 여성", "아내"), "아내": ("부인", "신부"), "남": ("남성", "남자"), "여": ("여성", "여자"),
}
_KOREAN_COUNT = re.compile(r"(?<![가-힣])(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스물|서른|마흔|쉰|예순|일흔|여든|아흔)+\s?(?:살|명|개|번|건|가구|사람)")


REGION_DIM = re.compile(r"행정구역|시도|시군구|읍면동|동읍면|지역")


def level(label: str) -> int:
    """Administrative level of a region label: 0 시도, 1 시·군, 2 구, 3 읍·면·동, 9 not a region. A question may name the
    region a cell sits in (영광군 for 백수면), not another region of the cell's own level (부산 for 서울)."""
    if label in OFFICIAL:
        return 0
    for n, ends in ((1, ("시", "군", "도")), (2, ("구",)), (3, ("읍", "면", "동", "리", "부"))):
        if label.endswith(ends):
            return n
    return 9


def core(name: str) -> str:
    return diverse.squash(re.sub(r"\(.*?\)", "", name)) or diverse.squash(name)


def bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def fit(name: str, phrase: str) -> int:
    """How well a phrase names a label: shared character bigrams, +2 for each USER_WORDS key that links them."""
    c, p = core(name), diverse.squash(phrase)
    synonyms = sum(1 for k, words in USER_WORDS.items() if k in c and any(diverse.squash(u) in p for u in words))
    return len(bigrams(c) & bigrams(p)) + 2 * synonyms


def siblings(dim: str, name: str, meta: dict) -> list[str]:
    """Other labels of the same dimension (the item list for the item), which a phrase must not fit as well."""
    pool_ = meta["items"] if dim == "항목" else next((c["labels"] for c in meta["classes"] if c["name"] == dim), [])
    return [o for o in pool_ if core(o) != core(name) and not diverse.is_total(o)]


def check_user_question(calc: CalcItem, template: str, question: str, terms: dict, item: str | None, meta: dict) -> list[str]:
    """T2-33 rule: years kept, no other digits or Korean counts, and every non-total label and the item named either
    as written or by a declared term that fits it better than any sibling label (so the cell stays pinned)."""
    problems = []
    years = set(re.findall(r"(\d{4})년", template))
    problems += [f"year {y} missing" for y in sorted(years) if y not in question]
    allowed = diverse.allowed_text_numbers(calc.required_facts + calc.aux_facts) | years | set(diverse._NUMBER.findall(template))
    if stray := [n for n in diverse._NUMBER.findall(question) if n not in allowed and n.replace(",", "") not in allowed]:
        problems.append(f"stray numbers {stray}")
    if m := _KOREAN_COUNT.search(question):
        problems.append(f"Korean count {m.group(0)!r}")
    words = [(dim, label) for f in calc.required_facts + calc.aux_facts if f.source_ref
             for dim, label in (f.dims or {}).items() if dim != "항목" and not diverse.is_total(label)]
    words = list(dict.fromkeys(words)) + ([("항목", item)] if item else [])
    named = {core(n) for _, n in words}
    # context words that are not a claim on another cell: the table's own name, survey and menu path, the cell's
    # labels as written (with parentheses, '내국인인구(15세이상)'), and the 시도/시군 a sub-region sits in
    context = diverse.squash(" ".join([meta["info"]["table_name"], meta["info"]["survey_name"], meta["info"]["menu_path"],
                                       *(n for _, n in words)]))
    fact_dims = {dim for f in calc.required_facts + calc.aux_facts if f.source_ref for dim in (f.dims or {})}
    for dim in fact_dims:  # a label of the same dimension that is not the cell's points the reader at another cell
        clash = [o for o in siblings(dim, "", meta) if len(core(o)) >= 3 and core(o) in diverse.squash(question)
                 and core(o) not in context and not any(core(o) in n or n in core(o) for n in named)
                 and not (REGION_DIM.search(dim) and level(o) < min((level(n) for d, n in words if d == dim), default=0))]
        if clash:
            problems.append(f"names another {dim} label {clash[:3]}")
    for dim, name in words:
        if diverse.label_ok(name, question) if dim != "항목" else core(name) in diverse.squash(question):
            continue
        phrase = terms.get(name)
        if not phrase or diverse.squash(phrase) not in diverse.squash(question):
            problems.append(f"{name!r} not named (term {phrase!r})")
            continue
        gold = fit(name, phrase)
        # a sub-category ('부엌_재래식_공동사용' under '부엌_재래식') is not a rival: the phrase names the whole group
        rivals = [o for o in siblings(dim, name, meta) if fit(o, phrase) >= gold and core(name) not in core(o)]
        if gold == 0 or rivals:
            problems.append(f"term {phrase!r} for {name!r} does not pin it" + (f" (also fits {rivals[:3]})" if rivals else ""))
    return problems


def same_cell(fact, df) -> list[float]:
    """Values another table holds for the fact's cell: same period, every non-total label (item name included) among
    the row's labels, all other labels totals. Empty when the table has no such cell (structure differs)."""
    wanted = {core(v) for v in (fact.dims or {}).values() if not diverse.is_total(v)}
    sub = df[(df.period == fact.period) & (df.value_kind == "ok")]
    out = []
    for row in sub.itertuples(index=False):
        labels = [getattr(row, f"dim{i}_label") for i in range(1, 9) if isinstance(getattr(row, f"dim{i}_label"), str)]
        have = {core(x) for x in [*labels, row.item_name]}
        if wanted <= have and all(diverse.is_total(x) or core(x) in wanted for x in labels):
            out.append(row.value)
    return out


def audit_alternates(l1: list[RetrievalItem], calc_by_qid: dict, values: Path, metas: dict) -> dict[str, list[str]]:
    """Drop an alternate key whose own cell for the question holds a different value (T2-33 review 14/21); keys whose
    tables lack a matching cell stay (another breakdown of the same count) and are listed as unverified."""
    import pyarrow.parquet as pq

    report = {"dropped": [], "unverified": [], "agreed": []}
    for item in l1:
        calc = calc_by_qid.get(item.qid)
        if not calc or len(item.required) != 1:
            continue
        facts = [f for f in calc.required_facts if f.source_ref and f.value is not None]
        keep = [item.required[0][0]]
        for key in item.required[0][1:]:
            df = pq.read_table(values / metas[key]["info"]["parquet"]).to_pandas()
            got = [same_cell(f, df) for f in facts]
            if any(g and all(abs(v - f.value) > 1e-6 * max(1, abs(f.value)) for v in g) for g, f in zip(got, facts)):
                report["dropped"].append(f"{item.qid} {key}")
                continue
            report["agreed" if all(got) else "unverified"].append(f"{item.qid} {key}")
            keep.append(key)
        item.required[0] = keep
    return report


def carry(prev: list[dict], rows: list[dict]) -> list[dict]:
    """Keep earlier Claude judgements by (qid, key) when the question was rewritten: a row judged before is not judged
    again, and a slotted table that dropped out of the new top 10 stays as a row so its slot is not lost."""
    judged = {(r["qid"], r["key"]): r for r in prev if r.get("auto") == "claude"}
    seen = set()
    for r in rows:
        seen.add((r["qid"], r["key"]))
        if not r["auto"] and (old := judged.get((r["qid"], r["key"]))):
            r |= {"slot": old["slot"], "auto": "claude"}
    qids = {r["qid"] for r in rows}
    kept = [{**r, "found_by": "carried", "rank": ""} for k, r in judged.items() if k not in seen and r["slot"].strip() and r["qid"] in qids]
    return rows + kept


def texts_by_qid() -> dict[str, dict]:
    return {t["qid"]: t for t in read_jsonl(TEXTS)}


def final_items(texts: dict, work: list[dict], metas: dict) -> tuple[list[tuple[dict, CalcItem]], list[str]]:
    out, problems = [], []
    for x in work:
        calc = CalcItem.model_validate(x["calc"])
        t = texts.get(x["qid"])
        if not t:
            problems.append(f"{x['qid']}: no text")
            continue
        # digits of the table name ('만 13세~만 15세이하') may be named too; they come from the registry, not the writer
        template = " ".join([calc.question, *re.findall(r"\d+", x["table_name"])])
        issues = check_user_question(calc, template, t["question"], t.get("terms", {}), item_name(calc, x), metas[calc.table_key])
        if issues:
            problems.append(f"{x['qid']}: {'; '.join(issues)}")
            continue
        out.append((x, calc.model_copy(update={"question": t["question"]})))
    return out, problems


def drafts_for(texts: dict, items: list[tuple[dict, CalcItem]]) -> list[dict]:
    drafts = [{"qid": x["qid"], "split": "test", "qtype": x["qtype"], "source": "v2", "question": c.question}
              for x, c in items if x["qtype"]]
    for t in texts.values():
        if t.get("extra"):
            d = {"qid": t["qid"], "split": "test", "qtype": t["qtype"], "source": "v2", "question": t["question"]}
            if t["qtype"] == "no_answer":
                d |= {"answerable": False, "no_answer_reason": t["no_answer_reason"]}
            drafts.append(d)
    return drafts


def pool(args) -> int:
    from evals.runners.retrieval import bm25_retrievers, load_records

    texts, work = texts_by_qid(), read_jsonl(WORK)
    metas = {r["info"]["key"]: r for r in read_jsonl(args.tables)}
    items, problems = final_items(texts, work, metas)
    for t in texts.values():
        if t.get("extra") and t["qtype"] == "no_answer" and (hits := absent_ok(t, metas)):
            problems.append(f"{t['qid']}: no_answer terms {t.get('absent')} found in {hits[:3]}")
    if problems:
        print("\n".join(problems))
        return 1
    calc_by_qid = {c.qid: c for _, c in items}
    retrievers = bm25_retrievers(load_records(args.tables), POOL_K)
    rows = []
    for d in drafts_for(texts, items):
        found = pool_rows(d, retrievers)
        if d.get("answerable", True) is False:
            rows += [{**r, "auto": ""} for r in found[:3]]
            continue
        gold = [calc_by_qid[d["qid"]].table_key] if d["qid"] in calc_by_qid else [k for s in texts[d["qid"]]["required"] for k in s]
        slot_of = {k: str(i + 1) for i, s in enumerate(texts.get(d["qid"], {}).get("required") or [gold]) for k in s}
        known = {r["key"] for r in found}
        found += [{"qid": d["qid"], "question": d["question"], "qtype": d["qtype"], "rank": "", "found_by": "gold",
                   "key": k, "table_name": metas[k]["info"]["table_name"], "period": metas[k]["info"]["period"], "slot": ""}
                  for k in slot_of if k not in known]
        for r in found:
            if r["key"] in slot_of:
                r |= {"slot": slot_of[r["key"]], "auto": "gold"}
            elif d["qid"] in calc_by_qid:
                r["auto"] = rule_out(calc_by_qid[d["qid"]], metas[r["key"]])
            else:
                r["auto"] = ""
        rows += found
    if POOL.exists():  # a rewrite keeps the judgements already made (T2-33)
        with POOL.open(encoding="utf-8-sig", newline="") as f:
            rows = carry(list(csv.DictReader(f)), rows)
    with POOL.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[*COLUMNS, "auto"])
        w.writeheader()
        w.writerows(rows)
    todo = sum(1 for r in rows if not r["auto"])
    print(f"{len(rows)} pool rows, {todo} to judge (empty `auto`) -> {POOL}")
    return 0


def finish(args) -> int:
    texts, work = texts_by_qid(), read_jsonl(WORK)
    full = {r["info"]["key"]: r for r in read_jsonl(args.tables)}
    items, problems = final_items(texts, work, full)
    if problems:
        print("\n".join(problems))
        return 1
    with POOL.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    l1 = apply_labels(drafts_for(texts, items), rows)
    alternates = audit_alternates(l1, {c.qid: c for _, c in items}, args.values, full)
    assign_splits(l1)
    check_items(l1, set(json.loads(line)["info"]["key"] for line in args.tables.read_text(encoding="utf-8").splitlines() if line.strip()))
    split = {i.qid: i.split for i in l1}
    calc_by_qid = {c.qid: c for _, c in items}
    l2 = []
    for i in l1:
        t = texts[i.qid]
        if i.qid in calc_by_qid:
            # the writer may override a code field when the question says more than the cells (a 시도 in a table name)
            fields = l2_from_facts(calc_by_qid[i.qid], i.question) | {k: t[k] for k in L2_CODE_FIELDS if k in t}
        else:
            fields = {k: t.get(k) for k in ("period_start", "period_end", "cycle")} | {"regions": t.get("regions", []), "years": t.get("years", [])}
        l2.append(QueryItem(qid=i.qid, split=i.split, question=i.question, answerable=i.answerable,
                            measures=t["measures"], dimensions=t["dimensions"], **fields))
    l3 = [c.model_copy(update={"split": split[c.qid.removesuffix("-REF")]}) for _, c in items]
    write_jsonl(OUT_L1, l1)
    write_jsonl(OUT_L2, l2)
    write_jsonl(OUT_L3, l3)
    metas = {json.loads(line)["info"]["key"]: json.loads(line)["info"] for line in args.tables.read_text(encoding="utf-8").splitlines() if line.strip()}
    primary = Counter(k for i in l1 for k in i.primary_keys)
    surveys = Counter(metas[k]["survey_name"] for i in l1 for k in i.primary_keys)
    judged = Counter(r["auto"].split(" ")[0] for r in rows)
    info = {
        "l1": {"items": len(l1), "answerable": sum(i.answerable for i in l1), "primary_tables": len(primary),
               "gold_keys": len({k for i in l1 for k in i.gold_keys}), "surveys": dict(surveys.most_common()),
               "qtypes": dict(Counter(i.qtype for i in l1)), "splits": dict(Counter(i.split for i in l1))},
        "l3": {"items": len(l3), "tables": len({c.table_key for c in l3}), "kinds": dict(Counter(f"{c.kind}/{c.type}" for c in l3)),
               "splits": dict(Counter(c.split for c in l3))},
        "alternates": {k: len(v) for k, v in alternates.items()}, "alternates_dropped": alternates["dropped"],
        "pool_rows": dict(judged), "labels": "questions, measures/dimensions and pool judgements by Claude (T2-32); facts, "
                                             "period/years/regions and rule:* pool rows by code",
        "tables.jsonl": sha256(args.tables),
        "sha256": {p.name: sha256(p) for p in (OUT_L1, OUT_L2, OUT_L3, POOL)},
    }
    OUT_L1.with_name("golden_v2.meta.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    with REVIEW.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["qid", "split", "qtype", "question", "required", "measures", "dimensions", "ok", "note"])
        for i in l1[::5]:
            w.writerow([i.qid, i.split, i.qtype, i.question, json.dumps(i.required, ensure_ascii=False),
                        texts[i.qid]["measures"], texts[i.qid]["dimensions"], "", ""])
    print(json.dumps({k: v for k, v in info.items() if k in ("l1", "l3", "pool_rows", "alternates")}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Golden set v2 for L1-L4 (T2-32)")
    parser.add_argument("step", choices=("draft", "pool", "finish"))
    parser.add_argument("--tables", type=Path, default=TABLES)
    parser.add_argument("--values", type=Path, default=ROOT / "data")
    parser.add_argument("--seed", type=int, default=32)
    args = parser.parse_args(argv)
    return {"draft": draft, "pool": pool, "finish": finish}[args.step](args)


if __name__ == "__main__":
    raise SystemExit(main())
