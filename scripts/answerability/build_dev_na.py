"""Answerability classifier (KOS-193 draft): the dev no-answer golden sets, written in the KOS-192 golden format.

v2 (default, 04-dataset-plan.md §6.1 / §6.3), one output file (--out, default eval/golden/kos193/dev_na_v2.jsonl):
  C  eval set C (--c-set, default scripts/answerability/v2/data/dev_na_v2_C.jsonl, 477 rows) converted to golden rows:
         qid "C-<id>", split dev, source v2, question = q; C4 -> answerable, required [[table]], qtype
         multi_condition when the spec has >= 2 of (dims, region names, years) else concept; C1 -> no_item, C2 ->
         no_period, C3 -> out_of_scope (qtype no_answer, required []).
  D  year variants of answerable dev questions (§6.3, reference set only, D9). For a question with one explicit year
     (build_data.single_year: note that its SKIP rule drops every question containing 월, so 월세 questions never get a
     D variant) that a gold table holds:
         no_period    the year -> a new year no gold table holds, sub-kind drawn by the np_near weights of §4.4
                      (between survey years 50% -> gap, a year another survey holds 30% -> sib, before the start /
                      after the end 20% -> before 10% + after 10%; tried in turn when a sub-kind has no passing year).
                      Every candidate year must pass the registry-wide check: year <= 2025, >= 30 registry tables hold
                      it, and no table holding it is close to any gold table, where close is (first hit is logged):
                        twin             same facts twin key (--facts facts.json of the mixft pool, when both tables
                                         have one) OR same facts name OR same normalized table name (no brackets /
                                         digits / region-level words / spaces / dashes)
                        same_name_words  same word set of table_name (v2/build_pairs.name_words)
                        name_sim         char-bigram Jaccard of table_name >= 0.45 (v2/check_gen.grams / jaccard;
                                         build_pairs has no name-similarity function)
                        items_axes       >= half of the gold items and the same non-region class axes
                                         (v2/spec_check.table_axes)
         period_ctrl  the year -> another year for which every required slot has >= 1 key holding it; each slot is
                      trimmed to those keys, and each kept key must pass the region check (a failing key is dropped;
                      an emptied slot rejects the year). Region check: the question's Hangul runs are matched (left
                      boundary = start of the run, longest match) against region names = registry region labels
                      ending in 시/군/구/도/읍/면/동 of length >= 3, their 시도 short forms (특별시 / 광역시 /
                      특별자치시 / 특별자치도 / 도 stripped), a 시도 short-form list (서울, 서울시, 경기 ...) and the
                      spec_check ALIASES keys; a 2-char short form needs a particle or the run end after it. Every
                      found name must be a label (or alias) of the key's region class when it has one, no METRO_FROM
                      head (check_gen; 인천광역, 세종 ...) may name a city that did not exist in the new year, and a
                      region-like run that resolves to no name (ends in 특별시/광역시/특별자치시/특별자치도, or 3-6
                      chars ending in 시/군/구/읍, minus words like 가구 / 인구 / 도시) skips the ctrl; such runs are
                      logged in stats (dev text only). The registry has no per-year label lists, so a region that a
                      table lists for some years only cannot be caught.
     qid "D-<src qid>-NP" / "-PC" (no clash with v1 qids); source = the source row's source when it is a golden
     source value, else v2.
  KOS-192 test gold tables (--c-exclude / --exclude files: only the `required` keys of split-test rows are read, never
  their text) are removed from both: C rows whose table is one, and D source rows whose gold keys meet them, are
  dropped and counted (§6.2); the counts are always printed (0 too).
  notes (every v2 row) is a JSON string: {"set", "kind", "sub", "source", ...}: C rows add group, style, table,
  relative, verify, pair_intact, mutation; D rows add check, n_tables_with_year, src, year_from, year_to. score_run.py
  groups by set, set:sub and set:relative on it.
v1 (--v1): the old dev-NA+ rows without the registry check, notes "src=... sub=..." (score_run set Dv1), written to
  --out (default eval/golden/kos193/dev_na_plus.jsonl); now with source "v2" and required as list[list[str]].
Every output row (v1 and v2) is checked against the golden rules (validate_golden mirrors evals/schemas.py
RetrievalItem); a failing row is dropped and counted. A stats line per set:sub is printed.
Sources of D / v1 (answerable rows only): the KOS-192 dev split (rows of split test are skipped before their question
is touched) and the older retrieval golden (eval/golden/retrieval_golden.jsonl). `required` is a list of slots; a flat
list of strings is read as ONE slot of alternatives. Rows without qid / id are skipped and counted. Siblings for "sib"
are the tables under the same menu parent in data/tables.jsonl. A missing --sources / --c-exclude / --c-set file is an
error unless --allow-missing.

    python scripts\\answerability\\build_dev_na.py --selftest
    python scripts\\answerability\\build_dev_na.py
    python scripts\\answerability\\build_dev_na.py --v1 --out eval\\golden\\kos193\\dev_na_plus.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "v2"))
from build_data import read_jsonl, swap_year, single_year, SUB_WEIGHTS, YEAR_MIN, YEAR_MAX  # noqa: E402
from cards import load_records, years_of  # noqa: E402
from build_pairs import name_words  # noqa: E402
from check_gen import METRO_FROM, grams, jaccard  # noqa: E402
from spec_check import ALIASES, _name_in, _region_class, table_axes  # noqa: E402

KOS192 = "retrieval_test_kos192"
NP_WEIGHTS = {"gap": 0.5, "sib": 0.3, "before": 0.1, "after": 0.1}  # §4.4 np_near: between 50 / other survey 30 / ends 20
D_YEAR_MAX = 2025
MIN_TABLES = 30
NAME_SIM = 0.45
C_REASON = {"C1": "no_item", "C2": "no_period", "C3": "out_of_scope"}
QTYPES = ("exact_name", "concept", "sibling", "multi_condition", "period_cycle", "multi_table", "ambiguous", "no_answer")
SOURCES = ("scenario", "mentor_poc", "phase1", "v2")
REASONS = ("out_of_scope", "no_period", "no_item")
FIELDS = {"qid", "split", "qtype", "source", "question", "answerable", "required", "no_answer_reason", "constraints",
          "phase1_id", "label_version", "notes"}
LEVEL_WORDS = re.compile(r"전국|시도|시군구|읍면동|동읍면|행정구역")
SIDO_SHORT = {"서울": ("서울특별시",), "부산": ("부산광역시",), "대구": ("대구광역시",), "인천": ("인천광역시",),
              "광주": ("광주광역시", "광주시"), "대전": ("대전광역시",), "울산": ("울산광역시",),
              "세종": ("세종특별자치시", "세종시"), "경기": ("경기도",), "강원": ("강원도", "강원특별자치도"),
              "충북": ("충청북도",), "충남": ("충청남도",), "전북": ("전라북도", "전북특별자치도"), "전남": ("전라남도",),
              "경북": ("경상북도",), "경남": ("경상남도",), "제주": ("제주특별자치도", "제주도")}
SIDO_TAIL = re.compile(r"(특별자치시|특별자치도|특별시|광역시)$|^((?:경기|강원|충청[남북]|전라[남북]|경상[남북]|제주))도$")
PARTICLES = ("에서는", "에서도", "에서", "에는", "으로", "까지", "부터", "보다", "지역", "에", "의", "은", "는", "이", "가",
             "을", "를", "와", "과", "로", "도", "만", "내", "별", "전체")
REST_OK = PARTICLES + ("시", "권")  # what may follow a 2-char 시도 short form in the same run
REGION_LIKE = re.compile(r"(특별시|광역시|특별자치시|특별자치도)$")
NOT_REGION = ("가구", "인구", "연구", "기구", "도구", "도시", "당시", "동시", "표시", "개시", "지시", "군인", "학군", "장군")


# ---- shared ---------------------------------------------------------------------------------------------------------

def slots_of(row: dict) -> list[list[str]]:
    """required as list[list[str]]: a list of slots; a flat list of strings is ONE slot of alternatives."""
    req = row.get("required") or []
    if req and all(isinstance(s, str) for s in req):
        return [list(dict.fromkeys(req))]
    return [[t for t in s if isinstance(t, str)] if isinstance(s, list) else [s] for s in req if isinstance(s, (str, list))]


def gold_keys(row: dict) -> list[str]:
    return list(dict.fromkeys(k for s in slots_of(row) for k in s))


def parent(rec: dict) -> str:
    path = (rec.get("info") or {}).get("menu_path") or ""
    return path.rsplit(" > ", 1)[0]


def ans_qtype(row: dict, slots: list[list[str]]) -> str:
    """The source qtype when it is valid for an answerable row with these slots, else concept / multi_table."""
    q = row.get("qtype")
    if q in QTYPES and q != "no_answer" and (q == "multi_table") == (len(slots) > 1):
        return q
    return "multi_table" if len(slots) > 1 else "concept"


def validate_golden(r: dict) -> list[str]:
    """Errors of one row against the golden rules (mirrors evals/schemas.py RetrievalItem, extra=forbid)."""
    err = [f"extra field {k}" for k in r if k not in FIELDS]
    for k in ("qid", "question"):
        if not isinstance(r.get(k), str) or not r.get(k):
            err.append(f"{k} not a non-empty str")
    if r.get("split") not in ("dev", "test"):
        err.append("bad split")
    if r.get("qtype") not in QTYPES:
        err.append(f"bad qtype {r.get('qtype')}")
    if r.get("source") not in SOURCES:
        err.append(f"bad source {r.get('source')}")
    if not isinstance(r.get("answerable"), bool):
        err.append("answerable not bool")
    req = r.get("required")
    if not isinstance(req, list) or not all(isinstance(s, list) and all(isinstance(k, str) for k in s) for s in req):
        err.append("required not list[list[str]]")
        req = []
    if r.get("no_answer_reason") not in (*REASONS, None):
        err.append("bad no_answer_reason")
    if "notes" in r and not isinstance(r["notes"], str):
        err.append("notes not str")
    if "constraints" in r and not (isinstance(r["constraints"], dict)
                                   and all(isinstance(k, str) and isinstance(v, str) for k, v in r["constraints"].items())):
        err.append("constraints not dict[str, str]")
    if "label_version" in r and not isinstance(r["label_version"], int):
        err.append("label_version not int")
    if r.get("answerable") is True:
        if not 1 <= len(req) <= 3 or not all(req):
            err.append("answerable needs 1..3 non-empty slots")
        if r.get("no_answer_reason") is not None:
            err.append("answerable with a reason")
        if r.get("qtype") == "no_answer":
            err.append("answerable with qtype no_answer")
        if (r.get("qtype") == "multi_table") != (len(req) > 1):
            err.append("multi_table iff > 1 slot")
    elif r.get("answerable") is False:
        if req:
            err.append("unanswerable with required")
        if r.get("no_answer_reason") is None:
            err.append("unanswerable without a reason")
        if r.get("qtype") != "no_answer":
            err.append("unanswerable qtype != no_answer")
    return err


def keep_valid(rows: list[dict], tally: Counter) -> list[dict]:
    good, seen = [], set()
    for r in rows:
        err = validate_golden(r)
        if r.get("qid") in seen:
            err.append("duplicate qid")
        if err:
            tally[f"invalid:{err[0]}"] += 1
            continue
        seen.add(r["qid"])
        good.append(r)
    return good


def answerable_sources(sources: list[Path], tally: Counter, test_keys: set[str] | None = None):
    """(path, row) of answerable source rows. Test-split rows are skipped before their question is touched; rows
    without qid / id, and (test_keys given) rows whose gold keys meet a KOS-192 test gold table, are counted."""
    seen_q = set()
    for path in sources:
        for r in read_jsonl(path):
            if r.get("split") == "test" or (path.name.startswith(KOS192) and r.get("split") != "dev"):
                continue  # the KOS-192 test split is never used
            if r.get("answerable") is False or not r.get("required"):  # old golden: no field = answerable
                continue
            qid = r.get("qid") or r.get("id")
            if not qid:
                tally["src_no_qid"] += 1
                continue
            if test_keys and set(gold_keys(r)) & test_keys:
                tally["d_dropped_kos192_test_table"] += 1
                continue
            if r["question"] in seen_q:
                continue
            seen_q.add(r["question"])
            yield path, {**r, "qid": qid}


def test_gold_keys(paths: list[Path]) -> set[str]:
    """Gold table keys of the KOS-192 test rows: `required` only (no question text is read)."""
    out: set[str] = set()
    for p in paths:
        for x in read_jsonl(p):
            if x.get("split") == "test":
                out |= set(gold_keys(x))
    return out


# ---- v1 -------------------------------------------------------------------------------------------------------------

def make(row: dict, years: dict[str, list[int]], fam: dict[str, list[str]], recs: dict, rng: random.Random) -> list[dict]:
    """no_period and period_ctrl rows (golden format) for one answerable source row."""
    keys = [k for k in gold_keys(row) if k in years]
    y = single_year(row["question"])
    if y is None or not keys or len(gold_keys(row)) != len(keys):
        return []
    holders = [k for k in keys if y in years[k]]
    if not holders:
        return []
    ok_years = set().union(*(years[k] for k in keys))
    g = years[holders[0]]
    sibs = [k for k in fam.get(parent(recs[holders[0]]), []) if k not in keys and k in years]
    pools = {
        "sib": sorted(set().union(*(years[k] for k in sibs)) - ok_years) if sibs else [],
        "gap": sorted(set(range(g[0], g[-1] + 1)) - ok_years),
        "after": list(range(max(ok_years) + 1, max(ok_years) + 9)),
        "before": list(range(min(ok_years) - 10, min(ok_years))),
    }
    pools = {k: [v for v in vs if YEAR_MIN <= v <= YEAR_MAX] for k, vs in pools.items()}
    out = []
    subs = [k for k in SUB_WEIGHTS if pools[k]]
    slots = slots_of(row)
    base = {"split": "dev", "source": "v2"}
    if subs:
        sub = rng.choices(subs, weights=[SUB_WEIGHTS[k] for k in subs])[0]
        new = rng.choice(pools[sub])
        out.append({"qid": f"{row['qid']}-NP", **base, "qtype": "no_answer",
                    "question": swap_year(row["question"], y, new), "answerable": False, "required": [],
                    "no_answer_reason": "no_period", "notes": f"src={row['qid']} year {y}->{new} sub={sub}"})
    others = [v for v in g if v != y]
    if others:
        new = rng.choice(others)
        out.append({"qid": f"{row['qid']}-PC", **base, "qtype": ans_qtype(row, slots),
                    "question": swap_year(row["question"], y, new), "answerable": True, "required": slots,
                    "no_answer_reason": None, "notes": f"src={row['qid']} year {y}->{new} sub=ctrl"})
    return out


def build(sources: list[Path], tables: Path, seed: int) -> tuple[list[dict], dict]:
    recs = load_records(tables)
    years = {k: v for k, v in ((k, years_of(r.get("periods", []))) for k, r in recs.items()) if v}
    fam: dict[str, list[str]] = {}
    for k, r in recs.items():
        fam.setdefault(parent(r), []).append(k)
    rng = random.Random(seed)
    rows, src, tally = [], Counter(), Counter()
    for path, r in answerable_sources(sources, tally):
        made = make(r, years, fam, recs, rng)
        src[path.name] += bool(made)
        rows += made
    rows = keep_valid(rows, tally)
    stats = {"sources_used": dict(src), "rows": len(rows), "tally": dict(tally),
             "kinds": dict(Counter(x["notes"].rsplit("sub=", 1)[1] for x in rows))}
    return rows, stats


# ---- v2: registry checks (D) ----------------------------------------------------------------------------------------

def norm_name(name: str) -> str:
    """Twin fallback: the table name without brackets, digits, region-level words, spaces and dashes."""
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", name)
    s = LEVEL_WORDS.sub("", re.sub(r"\d", "", s))
    return re.sub(r"[\s\-–—_·,]", "", s)


def strip_particles(tok: str) -> str:
    for _ in range(2):
        for p in PARTICLES:
            if tok.endswith(p) and len(tok) - len(p) >= 2:
                tok = tok[: -len(p)]
                break
    return tok


def region_like(tok: str) -> bool:
    if REGION_LIKE.search(tok):
        return True
    return 3 <= len(tok) <= 6 and tok[-1] in "시군구읍" and not tok.endswith(NOT_REGION)


class Registry:
    """Per-table facts for the §6.3 checks, computed once."""

    def __init__(self, recs: dict[str, dict], facts: dict | None = None):
        self.recs = recs
        self.years = {k: v for k, v in ((k, years_of(r.get("periods", []))) for k, r in recs.items()) if v}
        self.holders: dict[int, list[str]] = {}
        for k, ys in self.years.items():
            for y in ys:
                self.holders.setdefault(y, []).append(k)
        self.fam: dict[str, list[str]] = {}
        for k, r in recs.items():
            self.fam.setdefault(parent(r), []).append(k)
        facts = facts or {}
        self.n_without_facts = sum(k not in facts for k in recs) if facts else None
        self.ftwin = {k: (facts.get(k) or {}).get("twin") for k in recs}
        self.fname = {k: (facts.get(k) or {}).get("name") for k in recs}
        self.nname = {k: norm_name(r["info"]["table_name"]) for k, r in recs.items()}
        self.grams = {k: grams(r["info"]["table_name"]) for k, r in recs.items()}
        self.words = {k: name_words(r) for k, r in recs.items()}
        self.items = {k: set(r.get("items") or []) for k, r in recs.items()}
        self._axes: dict[str, frozenset] = {}
        # region names: surface -> full names it may stand for
        names: dict[str, set[str]] = {}
        for r in recs.values():
            for lab in (_region_class(r) or {}).get("labels") or []:
                if len(lab) >= 3 and lab[-1] in "시군구도읍면동":
                    names.setdefault(lab, set()).add(lab)
                    m = SIDO_TAIL.search(lab)
                    if m:
                        short = lab[: m.start()] if m.group(1) else m.group(2)
                        if len(short) >= 2:
                            names.setdefault(short, set()).add(lab)
        for short, fulls in SIDO_SHORT.items():
            names.setdefault(short, set()).update(fulls)
            for f in fulls:
                names.setdefault(f, set()).add(f)
            if fulls[0].endswith(("특별시", "광역시", "특별자치시")):
                names.setdefault(short + "시", set()).update(fulls)
        for k, al in ALIASES.items():
            names.setdefault(k, set()).update({k, *al})
        self.region_names_map = names
        self.region_surfaces = sorted(names, key=len, reverse=True)
        self.unresolved: Counter = Counter()

    def axes(self, k: str) -> frozenset:
        if k not in self._axes:
            self._axes[k] = frozenset(table_axes(self.recs[k]))
        return self._axes[k]

    def close(self, gold: str, other: str) -> str | None:
        """Why `other` may answer what `gold` answers (first rule hit), or None."""
        ft = self.ftwin[gold]
        if ((ft is not None and ft == self.ftwin[other]) or self.nname[gold] == self.nname[other]
                or (self.fname[gold] and self.fname[gold] == self.fname[other])):
            return "twin"
        if self.words[gold] == self.words[other]:
            return "same_name_words"
        if jaccard(self.grams[gold], self.grams[other]) >= NAME_SIM:
            return "name_sim"
        gi = self.items[gold]
        if gi and len(gi & self.items[other]) >= 0.5 * len(gi) and self.axes(gold) == self.axes(other):
            return "items_axes"
        return None

    def year_check(self, y: int, gold: list[str]) -> tuple[bool, str, int]:
        """(ok, why, n tables holding y) for a candidate no_period year."""
        hs = self.holders.get(y, [])
        if not YEAR_MIN <= y <= D_YEAR_MAX:
            return False, "year_range", len(hs)
        if len(hs) < MIN_TABLES:
            return False, "few_tables", len(hs)
        for h in hs:
            if h in gold:
                return False, "gold_holds", len(hs)
            for g in gold:
                why = self.close(g, h)
                if why:
                    return False, why, len(hs)
        return True, "registry_clear", len(hs)

    def region_tokens(self, q: str) -> tuple[list[tuple[str, set[str]]], list[str]]:
        """(resolved [(surface, full names)], unresolved region-like runs) of a question."""
        found, bad = [], []
        for run in re.findall(r"[가-힣]+", q):
            hit = None
            for s in self.region_surfaces:
                if run.startswith(s):
                    rest = run[len(s):]
                    if len(s) >= 3 or not rest or rest.startswith(REST_OK):
                        hit = s
                        break
            if hit:
                found.append((hit, self.region_names_map[hit]))
            elif region_like(strip_particles(run)):
                bad.append(strip_particles(run))
        return found, bad

    def region_names(self, q: str) -> list[str]:
        return [s for s, _ in self.region_tokens(q)[0]]

    def region_ok(self, q: str, key: str, year: int) -> tuple[bool, str]:
        found, bad = self.region_tokens(q)
        if bad:
            self.unresolved.update(bad)
            return False, "unresolved region " + ",".join(bad)
        for head, since in METRO_FROM:
            if year < since and (head in q or any(f.startswith(head) for _, fulls in found for f in fulls)):
                return False, f"{head} not before {since}"
        rc = _region_class(self.recs[key])
        if rc is None:
            return True, "no_region_class"
        labels = set(rc.get("labels") or [])
        for s, fulls in found:
            if not (_name_in(s, labels) or any(_name_in(f, labels) for f in fulls)):
                return False, f"region {s} not in table"
        return (True, "regions_ok:" + ",".join(s for s, _ in found)) if found else (True, "no_region_in_question")


def src_source(row: dict) -> str:
    return row.get("source") if row.get("source") in SOURCES else "v2"


def make_d(row: dict, reg: Registry, rng: random.Random, tally: Counter, source_name: str = "") -> list[dict]:
    """D rows (no_period with the registry check, period_ctrl with the slot / region checks) for one answerable row."""
    keys = [k for k in gold_keys(row) if k in reg.years]
    y = single_year(row["question"])
    if y is None or not keys or len(gold_keys(row)) != len(keys):
        return []
    holders = [k for k in keys if y in reg.years[k]]
    if not holders:
        return []
    years = reg.years
    ok_years = set().union(*(years[k] for k in keys))
    g = years[holders[0]]
    sibs = [k for k in reg.fam.get(parent(reg.recs[holders[0]]), []) if k not in keys and k in years]
    pools = {
        "gap": sorted(set(range(g[0], g[-1] + 1)) - ok_years),
        "sib": sorted(set().union(*(years[k] for k in sibs)) - ok_years) if sibs else [],
        "before": list(range(min(ok_years) - 10, min(ok_years))),
        "after": list(range(max(ok_years) + 1, max(ok_years) + 9)),
    }
    pools = {k: [v for v in vs if YEAR_MIN <= v <= D_YEAR_MAX] for k, vs in pools.items()}
    base = {"split": "dev", "source": src_source(row)}
    meta = {"set": "D", "source": source_name, "src": row["qid"], "year_from": y}
    out = []
    subs = [k for k in NP_WEIGHTS if pools[k]]
    picked = None
    while subs and picked is None:
        sub = rng.choices(subs, weights=[NP_WEIGHTS[k] for k in subs])[0]
        cand = pools[sub][:]
        rng.shuffle(cand)
        for new in cand:
            ok, why, n = reg.year_check(new, keys)
            if ok:
                picked = (sub, new, n)
                break
            tally[f"np_year_rejected:{why}"] += 1
        subs.remove(sub)
    if picked:
        sub, new, n = picked
        note = {**meta, "kind": "no_period", "sub": sub, "year_to": new, "n_tables_with_year": n,
                "check": f"registry_clear(<= {D_YEAR_MAX}, >= {MIN_TABLES} tables; no twin / same_name_words / "
                         f"name_sim >= {NAME_SIM} / items_axes table holds it)"}
        out.append({"qid": f"D-{row['qid']}-NP", **base, "qtype": "no_answer",
                    "question": swap_year(row["question"], y, new), "answerable": False, "required": [],
                    "no_answer_reason": "no_period", "notes": json.dumps(note, ensure_ascii=False)})
    elif any(pools.values()):
        tally["np_no_year_passed"] += 1
    slots = slots_of(row)
    cands = sorted(v for v in set().union(*(years[k] for k in keys)) - {y}
                   if all(any(v in years[k] for k in s) for s in slots))
    rng.shuffle(cands)
    for new in cands:
        trimmed, whys = [], []
        for s in slots:
            kept = []
            for k in s:
                if new in years[k]:
                    ok, why = reg.region_ok(row["question"], k, new)
                    (kept.append(k) if ok else None)
                    whys.append(why)
            trimmed.append(kept)
        if not all(trimmed):
            tally["ctrl_year_rejected:region"] += 1
            continue
        note = {**meta, "kind": "period_ctrl", "sub": "ctrl", "year_to": new,
                "n_tables_with_year": len(reg.holders.get(new, [])),
                "check": f"every slot holds {new}; " + "; ".join(dict.fromkeys(whys))}
        out.append({"qid": f"D-{row['qid']}-PC", **base, "qtype": ans_qtype(row, trimmed),
                    "question": swap_year(row["question"], y, new), "answerable": True, "required": trimmed,
                    "no_answer_reason": None, "notes": json.dumps(note, ensure_ascii=False)})
        break
    else:
        tally["ctrl_skipped_region" if cands else "ctrl_no_other_year"] += 1
    return out


# ---- v2: eval set C -------------------------------------------------------------------------------------------------

def c_qtype(spec: dict) -> str:
    n = (bool(spec.get("dims")) + bool((spec.get("region") or {}).get("names"))
         + bool((spec.get("time") or {}).get("years")))
    return "multi_condition" if n >= 2 else "concept"


def convert_c(rows: list[dict], test_keys: set[str], tally: Counter) -> list[dict]:
    tally["c_dropped_kos192_test_table"] += 0
    out = []
    for r in rows:
        s = r.get("set")
        if s not in ("C1", "C2", "C3", "C4"):
            tally["c_bad_set"] += 1
            continue
        if r.get("table") in test_keys:
            tally["c_dropped_kos192_test_table"] += 1
            continue
        sub = (r.get("mutation") or {}).get("type") or r.get("kind")
        note = {"set": s, "kind": r.get("kind"), "sub": sub, "source": "C", "group": r.get("group"),
                "style": r.get("style"), "table": r.get("table"), "relative": r.get("relative"),
                "verify": r.get("verify"), "pair_intact": r.get("pair_intact"), "mutation": r.get("mutation")}
        row = {"qid": f"C-{r['id']}", "split": "dev", "source": "v2", "question": r["q"]}
        if s == "C4":
            row.update(qtype=c_qtype(r.get("spec") or {}), answerable=True, required=[[r["table"]]],
                       no_answer_reason=None)
        else:
            row.update(qtype="no_answer", answerable=False, required=[], no_answer_reason=C_REASON[s])
        row["notes"] = json.dumps(note, ensure_ascii=False)
        out.append(row)
    return out


def build_v2(sources: list[Path], tables: Path, c_set: Path | None, exclude: list[Path], seed: int,
             facts: dict | None = None, with_d: bool = True) -> tuple[list[dict], dict]:
    tally: Counter = Counter()
    rows: list[dict] = []
    test_keys = test_gold_keys(exclude)
    if c_set is not None:
        rows += convert_c(read_jsonl(c_set), test_keys, tally)
    src = Counter()
    reg = None
    if with_d:
        reg = Registry(load_records(tables), facts)
        rng = random.Random(seed)
        tally["d_dropped_kos192_test_table"] += 0
        for path, r in answerable_sources(sources, tally, test_keys):
            made = make_d(r, reg, rng, tally, path.name)
            src[path.name] += bool(made)
            rows += made
    good = keep_valid(rows, tally)
    by = Counter()
    for r in good:
        n = json.loads(r["notes"])
        by[f"{n['set']}:{n['sub']}:{'ans' if r['answerable'] else r['no_answer_reason']}"] += 1
    stats = {"rows": len(good), "answerable": sum(r["answerable"] for r in good),
             "by_set": dict(sorted(Counter(json.loads(r["notes"])["set"] for r in good).items())),
             "by_set_sub": dict(sorted(by.items())), "d_sources_used": dict(src),
             "c_exclude_files": [str(p) for p in exclude], "n_test_gold_keys": len(test_keys),
             "c_dropped_kos192_test_table": tally.get("c_dropped_kos192_test_table", 0),
             "d_dropped_kos192_test_table": tally.get("d_dropped_kos192_test_table", 0),
             "tally": dict(sorted(tally.items())),
             "twins_from": "facts + normalized table name" if facts else "normalized table name",
             "registry_tables_without_facts": reg.n_without_facts if reg else None,
             "region_unresolved_tokens": dict(reg.unresolved.most_common(30)) if reg else {}}
    return good, stats


# ---- selftest -------------------------------------------------------------------------------------------------------

def selftest() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # v1
        recs = [
            {"info": {"key": "A", "table_name": "a", "menu_path": "x > y > a"}, "periods": ["2000", "2005", "2010"]},
            {"info": {"key": "B", "table_name": "b", "menu_path": "x > y > b"}, "periods": ["2011", "2012"]},
            {"info": {"key": "C", "table_name": "c", "menu_path": "x > z > c"}, "periods": ["2020"]},
        ]
        (td / "tables.jsonl").write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
        k = td / "retrieval_test_kos192.jsonl"
        k.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [
            {"qid": "D1", "split": "dev", "qtype": "multi_condition", "source": "s", "question": "2005년 a 값",
             "answerable": True, "required": [["A"]], "no_answer_reason": None},
            {"qid": "T1", "split": "test", "qtype": "multi_condition", "source": "s", "question": "2005년 a 값",
             "answerable": True, "required": [["A"]], "no_answer_reason": None},
            {"qid": "D2", "split": "dev", "qtype": "no_answer", "source": "s", "question": "2005년 없음",
             "answerable": False, "required": [], "no_answer_reason": "no_item"},
            {"qid": "D3", "split": "dev", "qtype": "concept", "source": "s", "question": "c 값",
             "answerable": True, "required": [["C"]], "no_answer_reason": None},
            {"split": "dev", "qtype": "concept", "source": "s", "question": "2005년 a 값 다시",
             "answerable": True, "required": [["A"]], "no_answer_reason": None}]), encoding="utf-8")
        rows, st = build([k], td / "tables.jsonl", 0)
        assert {r["qid"] for r in rows} == {"D1-NP", "D1-PC"}, rows
        assert st["tally"]["src_no_qid"] == 1 and all(not validate_golden(r) for r in rows)
        np_ = next(r for r in rows if r["qid"] == "D1-NP")
        assert np_["answerable"] is False and np_["no_answer_reason"] == "no_period" and np_["required"] == []
        new = int(np_["notes"].split("->")[1].split()[0])
        assert new not in (2000, 2005, 2010) and str(new) in np_["question"]
        pc = next(r for r in rows if r["qid"] == "D1-PC")
        assert pc["answerable"] and pc["required"] == [["A"]] and ("2000" in pc["question"] or "2010" in pc["question"])
        assert all(r["split"] == "dev" and r["source"] == "v2" for r in rows)
        # flat required = one slot of alternatives
        assert slots_of({"required": ["A", "B"]}) == [["A", "B"]] and slots_of({"required": [["A"], ["B"]]}) == [["A"], ["B"]]
        assert slots_of({"required": [["A", "B"]]}) == [["A", "B"]] and gold_keys({"required": ["A", "B"]}) == ["A", "B"]

        # v2 fake registry
        region = lambda labs: {"name": "행정구역별(시군구)", "labels": labs, "n_labels": len(labs),  # noqa: E731
                               "levels": {"전국": 1, "시도": 2, "시군구": 1}}
        age = {"name": "가구주의 연령별", "labels": ["30 ~ 34세", "35 ~ 39세"], "n_labels": 2, "levels": {}}

        def rec(key, name, years, mp, labs=None, items=None):
            cls = ([region(labs)] if labs else []) + [age]
            return {"info": {"key": key, "table_name": name, "menu_path": mp}, "prd_se": ["Y"],
                    "periods": [str(y) for y in years], "items": items or ["x"], "classes": cls}

        labs = ["전국", "서울특별시", "수원시", "세종특별자치시", "이동", "부부", "중구", "용인시"]
        recs2 = [rec("G", "가구주의 연령별 점유형태별 가구 - 시군구", [2000, 2005, 2010], "m > census > G", labs,
                     ["자가", "전세"]),
                 rec("S", "점유형태별 가구주의 연령별 가구 - 시군구", [2003], "m > census > S", labs, ["a"]),
                 rec("G2", "혼인상태별 인구 - 시도", [2000, 2005, 2010], "m > other > G2", ["전국", "서울특별시"],
                     ["인구"]),
                 rec("G3", "주택 종류별 주택 - 시도", [2000, 2005], "m > h > G3", ["전국", "서울특별시", "수원시"],
                     ["주택"]),
                 rec("G4", "거처 종류별 주택 - 시도", [2005, 2010], "m > h2 > G4", ["전국", "서울특별시", "수원시"],
                     ["거처"])]
        for i in range(40):  # unrelated tables: 2003, 2007, 2012 held by 40 tables, 2004 by 5
            ys = [2003, 2007, 2012] + ([2004] if i < 5 else [])
            recs2.append(rec(f"F{i:02d}", f"무관 통계표 번호 {i}", ys, f"z > f{i} > F{i:02d}", None, [f"i{i}"]))
        reg = Registry({r["info"]["key"]: r for r in recs2})
        assert reg.year_check(2003, ["G"]) == (False, "same_name_words", 41)       # sib year: a same-name table holds it
        assert reg.year_check(2004, ["G"]) == (False, "few_tables", 5)            # < 30 tables hold it
        assert reg.year_check(2007, ["G"]) == (True, "registry_clear", 40)
        assert reg.year_check(2030, ["G"])[1] == "year_range"
        assert reg.close("G", "F00") is None and norm_name("성별 인구 - 시군구(2015)") == norm_name("성별 인구-시도")
        # twins: facts twin (both present), or normalized name even when only one side has facts
        tw = Registry({r["info"]["key"]: r for r in recs2}, {"G": {"twin": "t", "name": "n1"}, "F01": {"twin": "t"}})
        assert tw.close("G", "F01") == "twin" and tw.year_check(2007, ["G"])[1] == "twin"
        assert tw.n_without_facts == len(recs2) - 2
        sim = {**recs2[5], "info": {**recs2[5]["info"], "table_name": "가구주의 연령별 점유형태별 가구 - 시도"}}
        tw2 = Registry({r["info"]["key"]: r for r in recs2[:5] + [sim]}, {"G": {"twin": "t"}})
        assert tw2.close("G", "F00") == "twin"  # G has a facts twin, F00 none: the normalized names still match
        fn = Registry({r["info"]["key"]: r for r in recs2}, {"G": {"name": "같은"}, "F02": {"name": "같은"}})
        assert fn.close("G", "F02") == "twin"
        ax = {**recs2[5], "items": ["자가", "x"], "info": {**recs2[5]["info"]}}
        assert Registry({r["info"]["key"]: r for r in recs2[:5] + [ax]}).close("G", "F00") == "items_axes"
        # region detection: labels / short forms / aliases with a boundary; ordinary words are not regions
        assert reg.region_names("2005년 수원시 서울특별시 인구") == ["수원시", "서울특별시"]
        assert reg.region_names("2005년 서울의 부부 이동 인구") == ["서울"]
        assert reg.region_names("2005년 서울대 졸업 인구") == []               # 2-char short form needs a boundary
        assert reg.region_names("2010년 세종 인구, 경기도 가구") == ["세종", "경기도"]
        assert reg.region_tokens("2005년 안양시 가구 수")[1] == ["안양시"]      # region-like, unresolved
        assert reg.region_tokens("2005년 일반가구 총인구 대도시")[1] == []
        ok, why = reg.region_ok("2010년 세종 30대 가구주", "G", 2010)
        assert not ok and "세종" in why                                         # short form + METRO_FROM
        assert reg.region_ok("2000년 서울 30대 가구주", "G", 2000) == (True, "regions_ok:서울")
        assert not reg.region_ok("2000년 안양시 30대 가구주", "G", 2000)[0]

        src_rows = [
            {"qid": "K1", "split": "dev", "qtype": "multi_condition", "source": "phase1", "answerable": True,
             "question": "2005년 수원시 30대 가구주 전세 가구 수", "required": [["G"]], "no_answer_reason": None},
            {"qid": "K2", "split": "dev", "qtype": "multi_condition", "source": "phase1", "answerable": True,
             "question": "2005년 수원시 미혼 인구", "required": [["G2"]], "no_answer_reason": None},
            {"qid": "K3", "split": "dev", "qtype": "multi_condition", "source": "odd", "answerable": True,
             "question": "2005년 세종 30대 가구주 전세 가구 수", "required": ["G"], "no_answer_reason": None},
            {"qid": "K4", "split": "dev", "qtype": "multi_table", "source": "phase1", "answerable": True,
             "question": "2005년 수원시 주택과 거처 수", "required": [["G3"], ["G4"]], "no_answer_reason": None},
            {"qid": "K5", "split": "dev", "qtype": "concept", "source": "phase1", "answerable": True,
             "question": "2005년 서울 주택 수", "required": [["G3", "G4"]], "no_answer_reason": None},
            {"qid": "K6", "split": "dev", "qtype": "concept", "source": "phase1", "answerable": True,
             "question": "2005년 용인시 가구 수", "required": [["HX"]], "no_answer_reason": None},
            {"qid": "T9", "split": "test", "qtype": "multi_condition", "source": "phase1", "answerable": True,
             "question": "2005년 서울특별시 30대 가구주 자가 가구", "required": [["G"]], "no_answer_reason": None}]
        kp = td / "retrieval_test_kos192_v2.jsonl"
        kp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in src_rows), encoding="utf-8")
        (td / "tables2.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs2), encoding="utf-8")
        for seed in range(30):
            tally = Counter()
            d = [x for path, r in answerable_sources([kp], tally) for x in make_d(r, reg, random.Random(seed), tally,
                                                                                     path.name)]
            assert not any("T9" in x["qid"] for x in d)  # test rows never used
            for x in (x for x in d if x["qid"].endswith("-NP")):
                n = json.loads(x["notes"])
                if n["src"] != "K1" and n["src"] != "K3":
                    continue
                assert n["year_to"] in (2007, 2012) and n["n_tables_with_year"] == 40 and str(n["year_to"]) in x["question"]
                assert n["sub"] == ("gap" if n["year_to"] == 2007 else "after") and n["set"] == "D"
            pcs = {x["qid"]: x for x in d if x["qid"].endswith("-PC")}
            assert "D-K1-PC" in pcs and json.loads(pcs["D-K1-PC"]["notes"])["check"].endswith("regions_ok:수원시")
            assert "D-K2-PC" not in pcs and tally["ctrl_skipped_region"] >= 1   # 수원시 not in G2: ctrl skipped
            assert "D-K3-PC" not in pcs  # 세종 (short form) did not exist in 2000 / 2010
            # K4: two slots; G3 holds 2000 only, G4 2010 only -> no year where every slot holds it
            assert "D-K4-PC" not in pcs and tally["ctrl_no_other_year"] >= 1
            # K5: one slot [G3, G4]: 2000 -> [G3], 2010 -> [G4] (trimmed)
            assert pcs["D-K5-PC"]["required"] in ([["G3"]], [["G4"]])
            yr = json.loads(pcs["D-K5-PC"]["notes"])["year_to"]
            assert pcs["D-K5-PC"]["required"] == ([["G3"]] if yr == 2000 else [["G4"]])
            assert all(not validate_golden(x) for x in d), [validate_golden(x) for x in d]
        k3 = next(x for x in d if x["qid"] == "D-K3-NP")
        assert k3["source"] == "v2" and pcs["D-K1-PC"]["required"] == [["G"]] and pcs["D-K1-PC"]["source"] == "phase1"

        # eval set C conversion
        spec2 = {"time": {"years": [2015]}, "region": {"names": ["수원시"]}, "dims": [], "measure": {"items": ["x"]}}
        mut = {"slot": "dims", "from": "a", "to": "b", "type": "A"}
        c_rows = [
            {"id": "G-E1", "table": "G", "group": "G-e1", "set": "C1", "kind": "no_item", "style": "casual", "q": "q1",
             "spec": spec2, "mutation": mut, "relative": None, "label": 0, "verify": "v1"},
            {"id": "G-E2", "table": "G", "group": "G-e1", "set": "C4", "kind": "ctrl", "style": "polite", "q": "q2",
             "spec": spec2, "mutation": None, "relative": "작년", "label": 1, "pair_intact": True},
            {"id": "G-E3", "table": "G", "group": "G-e2", "set": "C2", "kind": "no_period", "style": "casual",
             "q": "q3", "spec": spec2, "mutation": {"slot": "time", "from": "2015", "to": "2018", "type": "P-gap"},
             "label": 0},
            {"id": "oos-1", "table": "G", "group": "oos-1", "set": "C3", "kind": "oos", "style": "casual", "q": "q4",
             "spec": {"measure": {"text": "t"}}, "mutation": None, "label": 0},
            {"id": "G2-E1", "table": "G2", "group": "G2-e1", "set": "C4", "kind": "base", "style": "casual", "q": "q5",
             "spec": {"measure": {"items": ["인구"]}, "time": {"years": [2010]}}, "mutation": None, "label": 1},
            {"id": "H-E1", "table": "HX", "group": "H-e1", "set": "C4", "kind": "base", "style": "casual", "q": "q6",
             "spec": spec2, "mutation": None, "label": 1}]
        cp = td / "dev_na_v2_C.jsonl"
        cp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in c_rows), encoding="utf-8")
        excl = td / "retrieval_test_kos192.jsonl"
        excl.write_text(json.dumps({"qid": "T1", "split": "test", "question": "-", "required": [["HX"]]}), encoding="utf-8")
        out, st = build_v2([kp], td / "tables2.jsonl", cp, [excl], 0)
        cs = {r["qid"]: r for r in out if r["qid"].startswith("C-")}
        assert set(cs) == {"C-G-E1", "C-G-E2", "C-G-E3", "C-oos-1", "C-G2-E1"}, cs.keys()
        assert st["c_dropped_kos192_test_table"] == 1 and st["n_test_gold_keys"] == 1
        assert st["d_dropped_kos192_test_table"] == 1 and not any("K6" in r["qid"] for r in out)  # D sources too
        assert cs["C-G-E1"]["no_answer_reason"] == "no_item" and cs["C-G-E3"]["no_answer_reason"] == "no_period"
        assert cs["C-oos-1"]["no_answer_reason"] == "out_of_scope" and cs["C-oos-1"]["qtype"] == "no_answer"
        assert cs["C-G-E2"]["answerable"] and cs["C-G-E2"]["required"] == [["G"]]
        assert cs["C-G-E2"]["qtype"] == "multi_condition" and cs["C-G2-E1"]["qtype"] == "concept"
        n1 = json.loads(cs["C-G-E1"]["notes"])
        assert n1 == {"set": "C1", "kind": "no_item", "sub": "A", "source": "C", "group": "G-e1", "style": "casual",
                      "table": "G", "relative": None, "verify": "v1", "pair_intact": None, "mutation": mut}
        n2 = json.loads(cs["C-G-E2"]["notes"])
        assert n2["sub"] == "ctrl" and n2["relative"] == "작년" and n2["pair_intact"] is True
        assert json.loads(cs["C-G-E3"]["notes"])["sub"] == "P-gap"
        assert all(not validate_golden(r) for r in out) and any(r["qid"].startswith("D-") for r in out)
        assert st["by_set_sub"]["C1:A:no_item"] == 1 and st["by_set"]["C4"] == 2
        _, st0 = build_v2([kp], td / "tables2.jsonl", cp, [], 0)
        assert st0["c_dropped_kos192_test_table"] == 0 and st0["d_dropped_kos192_test_table"] == 0
        # the validator itself
        bad = {**cs["C-G-E2"], "no_answer_reason": "no_item", "extra": 1}
        assert "answerable with a reason" in validate_golden(bad) and "extra field extra" in validate_golden(bad)
        assert "multi_table iff > 1 slot" in validate_golden({**cs["C-G-E2"], "required": [["G"], ["G2"]]})
        assert "unanswerable with required" in validate_golden({**cs["C-G-E1"], "required": [["G"]]})
        assert "unanswerable qtype != no_answer" in validate_golden({**cs["C-G-E1"], "qtype": "concept"})
        assert "answerable needs 1..3 non-empty slots" in validate_golden({**cs["C-G-E2"], "required": [[]]})
        assert "bad source devna:s" in validate_golden({**cs["C-G-E2"], "source": "devna:s"})
        only_c, _ = build_v2([kp], td / "tables2.jsonl", cp, [excl], 0, with_d=False)
        assert all(r["qid"].startswith("C-") for r in only_c)
        # missing input files are an error unless --allow-missing
        try:
            need_files([td / "nope.jsonl"], "--sources", False)
            raise AssertionError("missing file not refused")
        except SystemExit:
            pass
        assert need_files([td / "nope.jsonl", kp], "--sources", True) == [kp]
    print("selftest ok")


def need_files(paths: list[Path], flag: str, allow_missing: bool) -> list[Path]:
    missing = [p for p in paths if not p.is_file()]
    if missing and not allow_missing:
        raise SystemExit(f"missing {flag} file(s): {[str(p) for p in missing]} (add --allow-missing to skip them)")
    for p in missing:
        print(f"skipped missing {flag} file: {p}", file=sys.stderr)
    return [p for p in paths if p.is_file()]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--v1", action="store_true", help="the old dev-NA+ build (no registry check), v1 output")
    p.add_argument("--sources", nargs="*", default=[f"eval/golden/kos192/{KOS192}.jsonl",
                                                     "eval/golden/retrieval_golden.jsonl"])
    p.add_argument("--tables", default="data/tables.jsonl")
    p.add_argument("--c-set", default="scripts/answerability/v2/data/dev_na_v2_C.jsonl",
                   help='eval set C file ("" = no C rows)')
    p.add_argument("--c-exclude", "--exclude", dest="exclude", nargs="*", default=[f"eval/golden/kos192/{KOS192}.jsonl"],
                   help="KOS-192 file(s): C rows / D sources on a gold table of a test row are dropped (required only)")
    p.add_argument("--allow-missing", action="store_true", help="skip missing --sources / --c-exclude files")
    p.add_argument("--no-d", action="store_true", help="v2: C rows only")
    p.add_argument("--facts", default=r"G:\내 드라이브\colab_project\kosis-embedder-train\data\mixft-v2\v2-strict\facts.json",
                   help="mixft facts.json for twin keys (missing file: twins by normalized table name only)")
    p.add_argument("--out", default=None, help="default eval/golden/kos193/dev_na_v2.jsonl (v1: dev_na_plus.jsonl)")
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    if a.selftest:
        selftest()
        return
    if not Path(a.tables).is_file():
        raise SystemExit(f"missing --tables {a.tables}")
    sources = need_files([Path(s) for s in a.sources], "--sources", a.allow_missing)
    if a.v1:
        rows, stats = build(sources, Path(a.tables), a.seed)
        out = Path(a.out or "eval/golden/kos193/dev_na_plus.jsonl")
    else:
        c_set = Path(a.c_set) if a.c_set else None
        if c_set is not None and not c_set.is_file():
            raise SystemExit(f"missing --c-set {c_set}")
        exclude = need_files([Path(x) for x in a.exclude], "--c-exclude", a.allow_missing)
        facts = None
        if a.facts and Path(a.facts).is_file():
            facts = json.loads(Path(a.facts).read_text(encoding="utf-8")).get("tables")
        else:
            print(f"no facts file ({a.facts}): twins by normalized table name only", file=sys.stderr)
        rows, stats = build_v2(sources, Path(a.tables), c_set, exclude, a.seed, facts, with_d=not a.no_d)
        out = Path(a.out or "eval/golden/kos193/dev_na_v2.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({**stats, "out": str(out)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
