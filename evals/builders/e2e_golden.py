"""T3-46 e2e golden set: ~600 questions by failure type, each turn with gold classification labels. No LLM.

    python -m evals.builders.e2e_golden            # e2e_golden.jsonl + e2e_golden_calc.jsonl (+ .meta.json)
    python -m evals.builders.e2e_golden --no-rules # skip the offline rule predictions (rule_pred)

Made from the wide calculation set (calculation_golden_wide.jsonl, T3-46 stage 1: 525 questions, 146 tables, 14
surveys, natural wording checked by code). Every number is a Parquet cell or computed from cells by code here; every
new question is a fixed template or a code edit of a checked question (a tail swapped, a year dropped), so the gold
labels are known by construction (plans/T3-46.md "구성"):

    numbers   (E2E-N)  value / compare questions as they are
    formula   (E2E-F)  trend (a % series -> pp_diff, never pct_change; a count -> pct_change), "몇 배" (ratio),
                       "연평균 증가율" (cagr)
    rewrite   (E2E-R)  two-turn conversations: 시도 / year carried over, relative year ("작년은?"), a reference
                       ("그 표에서 …"), a chart of the previous answer, a topic switch; multi-query (two tables in one
                       question, "둘의 차이는?" when the units match)
    classify  (E2E-C)  refusals (no_period / missing_value / out-of-scope item), causal ("왜 …"), evaluative
                       ("높은 편이야?" -> clarify), sums of a rate over years (not_aggregatable), a year left out
                       (default period)

Scenarios are AgentScenario (one turn = single-turn question); numbers are graded through calc_qid against
e2e_golden_calc.jsonl. rule_pred is today's rules run offline on the gold table (evals.evaluators.labels.rule_labels).
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from kosis_ai.schemas import Fact
from kosis_rag.shared.vocab import SIDO

from evals.builders.diverse import REGION_SUFFIX, SHORT as SHORT_NAMES, has_batchim, josa
from evals.builders.multiturn import OFFICIAL, REGION_DIM_WORDS, Cells, eun, region_of, stat_call
from evals.builders.phase1 import sha256
from evals.schemas import DATASETS, ROOT, TABLES, AgentScenario, CalcItem, TurnLabels, load_jsonl

WIDE = DATASETS / "calculation_golden_wide.jsonl"
DIVERSE = DATASETS / "calculation_golden_diverse.jsonl"
OUT = DATASETS / "e2e_golden.jsonl"
OUT_CALC = DATASETS / "e2e_golden_calc.jsonl"
QUOTA = {"numbers": 250, "rewrite_mq": 20, "mq_op": 20}  # the rest take every item their rule finds (capped below)
# T3-47: every required type aims at >= 20 items (plans/golden-quality-gate.md, minimum sample per type)
CAPS = {"ratio": 25, "cagr": 20, "E": 22, "X": 22, "Y": 22, "R": 22, "C": 22, "S": 22, "XE": 22, "XY": 22,
        "refuse": 60, "missing_value": 22, "causal": 22, "evaluative": 22, "default": 22, "sum_rate": 22,
        "share": 22, "extreme": 22, "same_name": 22, "partial_range": 22, "no_cell": 22}
# T3-47: follow-up wordings that are NOT the ones the context rules were tuned on (evals/builders/multiturn.py,
# context.LLM_EXAMPLES): the T3-46 copies of those templates leaked tuning into the test split. A wording that still
# matches a tuning template (evals.golden_gate.tuned) sends its scenario to dev.
ELLIPSIS = ["{r} 기준으로는?", "{r}도 알려줄래?", "{r} 숫자는 어때?"]
CORRECTION = ["{y}년이면?", "{y}년 자료로 볼래?", "{y}년 수치도 줘"]
RELATIVE = [("작년은?", -1), ("그 전 해는?", -1), ("재작년은?", -2)]
CHART = [("선 차트로 바꿔서 보여줄래?", "line"), ("막대 모양으로 다시 그려줄 수 있어?", "bar"), ("이거 막대 차트로 볼 수 있을까?", "bar")]
REFERENCE = ["아까 그 통계에서 ", "방금 그 자료로 "]
DEV_SHARE = 5  # one scenario in five (by sid hash) is dev; the rest is the held-out test split
TAIL = re.compile(r"\s*(?:은|는)?\s*(?:얼마나|몇\s*%|몇\s*배)[^,]*$")  # "… 얼마나 변했어?" tails of trend questions
VALUE_TAIL = re.compile(r"(?:은|는)\s*(?:전국\s*)?(?:몇\s*\S+|얼마)[^,]*$")  # "…는 몇 명이야?" / "…는 얼마야?"
YEAR = re.compile(r"(\d{4})년")
# Follow-up answers are read by multiturn.Cells (a whole table in Python, a linear scan per swap): small tables only.
# ponytail: row cap instead of a filtered Parquet read; lift it if the rewrite group needs more tables.
FOLLOW_UP_ROWS = 200_000


def small_tables(limit: int = FOLLOW_UP_ROWS) -> set[str]:
    import csv

    with (ROOT / "data" / "tables.csv").open(encoding="utf-8-sig", newline="") as f:
        return {r["key"] for r in csv.DictReader(f)
                if (ROOT / "data" / r["parquet"]).is_file() and int(r["rows"] or 0) <= limit}


def surveys() -> dict[str, str]:
    return {(i := json.loads(line)["info"])["key"]: i["survey_name"]
            for line in TABLES.read_text(encoding="utf-8").splitlines() if line.strip()}


def stratified(items: list[CalcItem], n: int, survey: dict[str, str], rng: random.Random) -> list[CalcItem]:
    """n items taken round-robin over surveys (each survey shuffled), so no survey crowds the others out."""
    groups: dict[str, list[CalcItem]] = defaultdict(list)
    for i in items:
        groups[survey.get(i.table_key, "?")].append(i)
    for g in groups.values():
        rng.shuffle(g)
    out, names = [], sorted(groups)
    while len(out) < n and any(groups.values()):
        for name in names:
            if groups[name] and len(out) < n:
                out.append(groups[name].pop())
    return out


def turn(question: str, tools: list[dict], labels: TurnLabels, **expect) -> dict:
    return {"question": question, "expect": {"tools": tools, "labels": labels.model_dump(exclude_none=True), **expect}}


def answer_tools(key: str) -> list[dict]:
    return [{"name": "search_tables"}, stat_call(key)]


def unit_of(item: CalcItem) -> str | None:
    return item.required_facts[0].unit if item.required_facts else None


def formula_labels(item: CalcItem) -> dict:
    """compare: a difference (pp_diff of percentages); trend: pp_diff on a % series (pct_change would be wrong),
    pct_change on a count."""
    pct = unit_of(item) == "%"
    if item.type == "compare":
        return {"formula": ["pp_diff" if pct else "diff"]}
    if item.type == "trend":
        return {"formula": ["pp_diff"], "formula_forbidden": ["pct_change"]} if pct else {"formula": ["pct_change"]}
    return {}


def answered(item: CalcItem, ftype: str, **extra) -> TurnLabels:
    return TurnLabels(failure_type=ftype, route="rag", reading="new", outcome="answered", qtype=item.type,
                      **formula_labels(item), **extra)


def derived(item: CalcItem, qid: str, question: str, value: float, unit: str, derivation: str) -> CalcItem:
    """The item with its derived fact (f3) replaced: same cell facts, a new computed value."""
    cells = [f for f in item.required_facts if f.source_ref]
    f3 = Fact(id="f3", value=round(value, 1), unit=unit, derivation=derivation, round=1)
    return item.model_copy(update={"qid": qid, "question": question, "required_facts": [*cells, f3], "split": None})


def ratio_question(item: CalcItem) -> str | None:
    """"…, A랑 B 차이는?" -> "…, A는 B의 몇 배야?" when the two labels end the question right before 차이."""
    fa, fb = (f.dims or {} for f in item.required_facts[:2])
    diff = [k for k in fa if fa.get(k) != fb.get(k)]
    if len(diff) != 1:
        return None
    a, b = fa[diff[0]], fb[diff[0]]
    m = re.search(re.escape(a) + r"\s*(?:이랑|랑|와|과|하고)\s*" + re.escape(b) + r"\s*(?:의\s*)?(?:차이|차)", item.question)
    if not m:
        return None
    return item.question[:m.start()] + f"{josa(a, '은는')} {b}의 몇 배야?"


def region_surface(question: str, official: str) -> str | None:
    """The way the question names the 시도 (official name, short name or name without its suffix)."""
    for s in (official, SHORT_NAMES.get(official), REGION_SUFFIX.sub("", official), *SIDO.get(official, [])):
        if s and len(s) >= 2 and s in question:
            return s
    return None


def compare_prefix(item: CalcItem) -> tuple[str, str, str, str] | None:
    """(text before the two labels, label a, label b, dim name) of a "…, A랑 B 차이는?" question, None otherwise."""
    fa, fb = (f.dims or {} for f in item.required_facts[:2])
    diff = [k for k in fa if fa.get(k) != fb.get(k)]
    if len(diff) != 1:
        return None
    a, b = fa[diff[0]], fb[diff[0]]
    m = re.search(re.escape(a) + r"\s*(?:이랑|랑|와|과|하고)\s*" + re.escape(b), item.question)
    if not m:
        return None
    return item.question[:m.start()].rstrip(" ,") , a, b, diff[0]


def siblings(cells: Cells, ref: str, dim: str) -> list:
    """Cells that differ from `ref` only in the dimension named `dim` (same item, period and other codes), numbers only."""
    src = cells.by_ref.get(ref)
    if src is None:
        return []
    out = []
    for c in cells.by_ref.values():
        if c.item_id != src.item_id or c.period != src.period or len(c.dims) != len(src.dims) or c.value_kind != "ok":
            continue
        if all(x.code == y.code for x, y in zip(src.dims, c.dims) if x.name != dim):  # src itself included
            out.append(c)
    return out


def label_of(cell, dim: str) -> str:
    return next((d.label.strip() for d in cell.dims if d.name == dim), "")


def is_total_label(label: str) -> bool:
    return label in TOTAL_LABELS or label.endswith("계")


TOTAL_LABELS = {"계", "합계", "총계", "전체", "소계", "전국", "총인구"}
SUBTOTAL_LABELS = ("시부", "읍부", "면부", "동부", "군부")  # national subtotals: unqualified, they name one cell
SIDO_ALIASES = sorted({a for n, al in SIDO.items() for a in (n, *al)}, key=len, reverse=True)


def names_sido(text: str) -> bool:
    return any(a in text for a in SIDO_ALIASES)


def measure_prefix(prefix: str) -> bool:
    """T3-47 review: a share / extreme question keeps its measure (>= 4 Hangul letters besides the year) and names no
    시도 (a 시도 there scopes the question, but the denominator is the national total)."""
    content = re.sub(r"\d{4}\s*년", "", prefix)
    return len(re.findall(r"[가-힣]", content)) >= 4 and not names_sido(content)


def averaged(item: CalcItem) -> bool:
    """An average measure: a share of averages is not a share (T3-47 review)."""
    return "평균" in item.question or any("평균" in str(v) for f in item.required_facts for v in (f.dims or {}).values())


def without_aggregates(parts: list) -> list:
    """Drop cells that are the sum of the others (세종시 = 동부 + 읍부 + 면부): a total under a non-total name."""
    parts = list(parts)
    while len(parts) >= 3:
        top = max(parts, key=lambda c: c.value)
        rest = sum(c.value for c in parts if c is not top)
        if not top.value or abs(top.value - rest) > 0.005 * abs(top.value):
            break
        parts.remove(top)
    return parts


def cell_at(cells: Cells, ref: str, *, region: str, year: str):
    """The cell of `ref` at another 시도 and year, whatever it holds (a symbol cell counts as present)."""
    src = cells.by_ref.get(ref)
    if src is None:
        return None
    for cell in cells.by_ref.values():
        if cell.item_id != src.item_id or len(cell.dims) != len(src.dims) or cell.period != year + src.period[4:]:
            continue
        if all((OFFICIAL.get(b.label.strip()) == region) if any(w in a.name for w in REGION_DIM_WORDS) else a.code == b.code
               for a, b in zip(src.dims, cell.dims)):
            return cell
    return None


def cell_fact(fid: str, cell, base: Fact) -> Fact:
    return base.model_copy(update={"id": fid, "value": cell.value, "period": cell.period, "source_ref": cell.ref(),
                                   "dims": {**(base.dims or {}), **{d.name: d.label for d in cell.dims}}})


def build(seed: int = 46) -> tuple[list[AgentScenario], list[CalcItem], dict]:
    from evals.golden_gate import ambiguous_refs, degenerate, tuned, tuned_shapes

    rng = random.Random(seed)
    survey = surveys()
    wide = load_jsonl(WIDE, CalcItem)
    small = small_tables()
    have = {i.qid for i in wide}
    extra = [i for i in load_jsonl(DIVERSE, CalcItem) if i.qid not in have
             and (ROOT / "data" / "values" / f"{i.table_key}.parquet").is_file()]
    # T3-47: items whose gold cell id names several cells (no single right value) or that cannot discriminate are out
    bad_refs, _ = ambiguous_refs([*wide, *extra])
    dropped = {"ambiguous_cell": sorted({i.qid for i in [*wide, *extra] if any(f.source_ref in bad_refs for f in i.required_facts + i.aux_facts)}),
               "degenerate": sorted({i.qid for i in [*wide, *extra] if degenerate(i)})}
    out_ids = set(dropped["ambiguous_cell"]) | set(dropped["degenerate"])
    wide = [i for i in wide if i.qid not in out_ids]
    extra = [i for i in extra if i.qid not in out_ids]
    answerable = [i for i in wide if i.kind == "answerable"]
    refusals = [i for i in [*wide, *extra] if i.kind == "not_answerable"]
    paired = answerable + [i for i in extra if i.kind == "answerable"]
    scenarios: list[dict] = []
    calc: dict[str, CalcItem] = {}
    count: Counter = Counter()
    graded_q: set[str] = set()  # questions already graded as a stand-alone last turn (S must not reuse them)

    def add(prefix: str, kind: str, turns: list[dict], *items: CalcItem) -> None:
        count[kind] += 1
        scenarios.append({"sid": f"E2E-{prefix}-{kind}-{count[kind]:03d}", "split": "test", "source": kind,
                          "notes": kind, "turns": turns})
        last = turns[-1]
        if last["expect"]["labels"].get("reading") in (None, "new"):
            graded_q.add(last["question"])
        for i in items:
            calc[i.qid] = i

    def full(kind: str) -> bool:
        return count[kind] >= CAPS.get(kind, 10**9)

    cells_cache: dict[str, Cells] = {}

    def cells(key: str) -> Cells:
        if key not in cells_cache:
            if len(cells_cache) > 40:  # whole small tables in memory: keep the cache bounded
                cells_cache.pop(next(iter(cells_cache)))
            cells_cache[key] = Cells(key)
        return cells_cache[key]

    # ① numbers: value and compare as written, round-robin over surveys
    plain = [i for i in answerable if i.type in ("value", "compare")]
    for item in stratified(plain, QUOTA["numbers"], survey, rng):
        add("N", item.type, [turn(item.question, answer_tools(item.table_key), answered(item, "numbers"),
                                  table_key=item.table_key, calc_qid=item.qid)], item)

    # ② formula: every trend (the % / count trap), "몇 배", "연평균 증가율", "비중" (share), "가장 많은" (extreme)
    for item in [i for i in answerable if i.type == "trend"]:
        add("F", "trend", [turn(item.question, answer_tools(item.table_key), answered(item, "formula"),
                                table_key=item.table_key, calc_qid=item.qid)], item)
    for item in [i for i in answerable if i.type == "compare" and unit_of(i) not in ("%", None)]:
        a, b = (f.value for f in item.required_facts[:2])
        q = ratio_question(item)
        if full("ratio") or q is None or not a or not b or a / b <= 0:
            continue
        new = derived(item, item.qid + "-RATIO", q, a / b, "배", "f1 / f2")
        add("F", "ratio", [turn(q, answer_tools(item.table_key),
                                answered(new, "formula").model_copy(update={"formula": ["ratio"]}),
                                table_key=item.table_key, calc_qid=new.qid)], new)
    for item in [i for i in answerable if i.type == "trend" and unit_of(i) != "%"]:
        first, last = item.required_facts[:2]
        years = int(last.period[:4]) - int(first.period[:4])
        if full("cagr") or not TAIL.search(item.question) or years < 2 or not first.value or not last.value \
                or first.value <= 0 or last.value <= 0:
            continue
        q = TAIL.sub("의 연평균 증가율은 얼마야?", item.question)
        new = derived(item, item.qid + "-CAGR", q, ((last.value / first.value) ** (1 / years) - 1) * 100, "%",
                      f"((f2 / f1) ** (1 / {years}) - 1) * 100")
        add("F", "cagr", [turn(q, answer_tools(item.table_key),
                               answered(new, "formula").model_copy(update={"formula": ["cagr"]}),
                               table_key=item.table_key, calc_qid=new.qid)], new)
    compares = [i for i in paired if i.type == "compare" and i.table_key in small and compare_prefix(i)]
    rng.shuffle(compares)
    for item in compares:
        if full("share") and full("extreme"):
            break
        prefix, a, b, dim = compare_prefix(item)
        if not measure_prefix(prefix):
            continue
        fa = item.required_facts[0]
        sib = siblings(cells(item.table_key), fa.source_ref, dim)
        base = next((c for c in sib if c.ref() == fa.source_ref), None)
        if base is None:
            continue
        total = [c for c in sib if is_total_label(label_of(c, dim))]
        # a sample size (사례수) or another unit is not a candidate; a sum of the others is a total by another name
        parts = without_aggregates([c for c in sib if not is_total_label(label_of(c, dim)) and "사례수" not in label_of(c, dim)
                                    and "사례수" not in c.item_name and c.unit_or_suffix() == base.unit_or_suffix()])
        if not full("share") and unit_of(item) not in ("%", None) and not averaged(item) and len(total) == 1 \
                and total[0].value and fa.value is not None:
            q = f"{prefix}, 전체에서 {josa(a, '이가')} 차지하는 비중은 몇 %야?"
            f1 = cell_fact("f1", next(c for c in sib if c.ref() == fa.source_ref), fa)
            f2 = cell_fact("f2", total[0], fa)
            new = item.model_copy(update={"qid": item.qid + "-SHARE", "question": q, "type": "share", "split": None,
                                          "required_facts": [f1, f2, Fact(id="f3", value=round(f1.value / f2.value * 100, 1),
                                                                          unit="%", derivation="f1 / f2 * 100", round=1)]})
            add("F", "share", [turn(q, answer_tools(item.table_key),
                                    TurnLabels(failure_type="formula", route="rag", reading="new", outcome="answered",
                                               qtype="share", formula=["share"]),
                                    table_key=item.table_key, calc_qid=new.qid)], new)
            continue
        values = [c.value for c in parts]
        if not full("extreme") and 3 <= len(parts) <= 40 and values.count(max(values)) == 1:
            top = max(parts, key=lambda c: c.value)
            word = "높아" if unit_of(item) == "%" else "많아"
            regional = any(w in dim for w in (*REGION_DIM_WORDS, "시군구", "지역"))
            q = f"{prefix}, {'어디가' if regional else '어떤 항목이'} 가장 {word}?"
            new = item.model_copy(update={"qid": item.qid + "-MAX", "question": q, "type": "extreme", "split": None,
                                          "required_facts": [cell_fact("f1", top, fa)]})
            add("F", "extreme", [turn(q, answer_tools(item.table_key),
                                      TurnLabels(failure_type="formula", route="rag", reading="new", outcome="answered",
                                                 qtype="extreme"), table_key=item.table_key, calc_qid=new.qid)], new)

    # ③ rewrite: follow-up turns on one-fact value questions (answers are swapped cells read by code)
    values = [i for i in paired if i.type == "value" and len(i.required_facts) == 1 and i.table_key in small]
    rng.shuffle(values)

    def synthetic_item(item: CalcItem, tag: str, resolved: str, cell) -> CalcItem:
        return CalcItem(qid=f"{item.qid}-{tag}-{cell.ref().split('|', 2)[2].replace('|', '-')}", kind="answerable",
                        type="value", question=resolved, table_key=item.table_key, table_sha256=item.table_sha256,
                        required_facts=[cell_fact("f1", cell, item.required_facts[0])], eda_traps=["synthetic_multiturn"])

    def carried(q: str, resolved: str, item: CalcItem, new: CalcItem) -> dict:
        labels = TurnLabels(failure_type="rewrite", route="rewrite", reading="carry_over", resolved_question=resolved,
                            outcome="answered", qtype="value")
        return turn(q, [stat_call(item.table_key)], labels, table_key=item.table_key, calc_qid=new.qid)

    def first_turn(item: CalcItem) -> dict:
        return turn(item.question, answer_tools(item.table_key), answered(item, "rewrite"),
                    table_key=item.table_key, calc_qid=item.qid)

    for item in values:
        if all(full(k) for k in ("E", "X", "Y", "XE", "XY")):
            break
        fact = item.required_facts[0]
        year = (fact.period or "")[:4]
        region = region_of(fact)
        surface = region and (("전국" if "전국" in item.question else None) if region[1] == "전국"
                              else region_surface(item.question, region[1]))
        has_year = year.isdigit() and f"{year}년" in item.question
        if region and surface and (not full("E") or (has_year and not full("XE"))):
            targets = [r for r in SIDO if r != region[1]]
            rng.shuffle(targets)
            for target in targets:
                if (cell := cells(item.table_key).swap(fact.source_ref, region=target)) is None:
                    continue
                short = SHORT_NAMES.get(target) or REGION_SUFFIX.sub("", target)
                q2 = rng.choice(ELLIPSIS).format(r=short)
                resolved = item.question.replace(surface, target, 1)
                # E and the 3-turn XE take turns on the items that support both (XE needs the year in the question)
                if not full("E") and (full("XE") or not has_year or count["E"] <= count["XE"]):
                    new = synthetic_item(item, "E", resolved, cell)
                    add("R", "E", [first_turn(item), carried(q2, resolved, item, new)], item, new)
                    break
                # 3 turns: correct the year, then move to another 시도 (both carried over)
                for back in (1, 2, 3):
                    y2 = str(int(year) - back)
                    c2 = cells(item.table_key).swap(fact.source_ref, year=y2)
                    c3 = c2 and cells(item.table_key).swap(c2.ref(), region=target)
                    if c2 is None or c3 is None:
                        continue
                    r2 = item.question.replace(f"{year}년", f"{y2}년", 1)
                    r3 = r2.replace(surface, target, 1)
                    n2, n3 = synthetic_item(item, "XE2", r2, c2), synthetic_item(item, "XE3", r3, c3)
                    add("R", "XE", [first_turn(item), carried(rng.choice(CORRECTION).format(y=y2), r2, item, n2),
                                    carried(q2, r3, item, n3)], item, n2, n3)
                    break
                break
        if has_year and not full("XY") and not (region and surface):
            # 3 turns without a 시도: correct the year, then step back one more year ("그 전 해는?")
            for back in (1, 2, 3):
                y2, y3 = str(int(year) - back), str(int(year) - back - 1)
                c2 = cells(item.table_key).swap(fact.source_ref, year=y2)
                c3 = cells(item.table_key).swap(fact.source_ref, year=y3)
                if c2 is None or c3 is None:
                    continue
                r2 = item.question.replace(f"{year}년", f"{y2}년", 1)
                r3 = item.question.replace(f"{year}년", f"{y3}년", 1)
                n2, n3 = synthetic_item(item, "XY2", r2, c2), synthetic_item(item, "XY3", r3, c3)
                add("R", "XY", [first_turn(item), carried(rng.choice(CORRECTION).format(y=y2), r2, item, n2),
                                carried("그 전 해는?", r3, item, n3)], item, n2, n3)
                break
            continue
        if has_year:
            if not full("X"):
                for back in (1, 2, 3, 5):
                    target = str(int(year) - back)
                    if (cell := cells(item.table_key).swap(fact.source_ref, year=target)) is not None:
                        resolved = item.question.replace(f"{year}년", f"{target}년", 1)
                        new = synthetic_item(item, "X", resolved, cell)
                        add("R", "X", [first_turn(item), carried(rng.choice(CORRECTION).format(y=target), resolved, item, new)], item, new)
                        break
            elif not full("Y"):
                phrase, step = rng.choice(RELATIVE)
                target = str(int(year) + step)
                if (cell := cells(item.table_key).swap(fact.source_ref, year=target)) is not None:
                    resolved = item.question.replace(f"{year}년", f"{target}년", 1)
                    new = synthetic_item(item, "Y", resolved, cell)
                    add("R", "Y", [first_turn(item), carried(phrase, resolved, item, new)], item, new)

    # R reference: a compare question of the same table asked after its value question, behind a reference phrase
    by_table: dict[str, dict[str, CalcItem]] = defaultdict(dict)
    for i in paired:
        by_table[i.table_key].setdefault(i.type, i)
    pairs = [(t["value"], t["compare"]) for t in by_table.values() if "value" in t and "compare" in t]
    rng.shuffle(pairs)
    for v, c in pairs[:CAPS["R"]]:
        q2 = rng.choice(REFERENCE) + c.question
        add("R", "R", [turn(v.question, answer_tools(v.table_key), answered(v, "rewrite"), table_key=v.table_key, calc_qid=v.qid),
                       turn(q2, [stat_call(c.table_key)], answered(c, "rewrite").model_copy(update={
                           "reading": "reference", "route": "rewrite", "resolved_question": q2}),
                           table_key=c.table_key, calc_qid=c.qid)], v, c)
    # C chart of the previous answer
    trends = [i for i in answerable if i.type == "trend"]
    rng.shuffle(trends)
    for item in trends[:CAPS["C"]]:
        q2, kind = rng.choice(CHART)
        add("R", "C", [turn(item.question, answer_tools(item.table_key), answered(item, "rewrite"),
                            table_key=item.table_key, calc_qid=item.qid),
                       turn(q2, [{"name": "make_chart", "args": {"table_key": item.table_key, "chart_type": kind}}],
                            TurnLabels(failure_type="rewrite", route="rewrite", reading="chart_only", chart_request=kind),
                            table_key=item.table_key)], item)
    # S topic switch: the second question is one no other scenario grades (T3-47: no double grading)
    unused = [i for i in plain if i.table_key in small and i.question not in graded_q]
    firsts = [i for i in plain if i.table_key in small]
    rng.shuffle(unused)
    rng.shuffle(firsts)
    for b in unused:
        if full("S"):
            break
        a = rng.choice([x for x in firsts if survey.get(x.table_key) != survey.get(b.table_key) and x.qid != b.qid] or [None])
        if a is None or b.question in graded_q:
            continue
        add("R", "S", [turn(a.question, answer_tools(a.table_key), answered(a, "rewrite"), table_key=a.table_key, calc_qid=a.qid),
                       turn(b.question, answer_tools(b.table_key), answered(b, "rewrite"), table_key=b.table_key, calc_qid=b.qid)], a, b)
    # MQ: two value questions of different surveys in one turn; "둘의 차이는?" when both are in one unit
    singles = [i for i in values if i.required_facts[0].value is not None]
    rng.shuffle(singles)
    made = Counter()
    for a, b in ((x, y) for x in singles for y in singles if x.qid < y.qid):
        if made["plain"] >= QUOTA["rewrite_mq"] and made["op"] >= QUOTA["mq_op"]:
            break
        if survey.get(a.table_key) == survey.get(b.table_key) or a.table_key == b.table_key:
            continue
        op = "diff" if unit_of(a) == unit_of(b) and made["op"] < QUOTA["mq_op"] else None
        if op is None and made["plain"] >= QUOTA["rewrite_mq"]:
            continue
        made["op" if op else "plain"] += 1
        qa, qb = a.question.rstrip("?？ "), b.question.rstrip("?？ ")
        question = f"{qa}, 그리고 {qb}?" + (" 둘의 차이는?" if op else "")
        labels = TurnLabels(failure_type="rewrite", route="rewrite", reading="new", sub_questions=[qa, qb], op=op,
                            outcome="answered")
        expect = {"calc_qids": [a.qid, b.qid], "table_key": b.table_key}
        if op:
            expect["combine_value"] = round(a.required_facts[0].value - b.required_facts[0].value, 6)
        add("R", "MQ", [turn(question, [{"name": "search_tables"}, {"name": "search_tables"}, stat_call(a.table_key),
                                        stat_call(b.table_key), *([{"name": "combine_values", "args": {"op": op}}] if op else [])],
                             labels, **expect)], a, b)

    # ④ classify
    rng.shuffle(refusals)
    symbol = [i for i in refusals if "missing_value" in i.allowed_reasons]
    picked = stratified(symbol, CAPS["missing_value"], survey, rng)
    rest = [i for i in refusals if i not in picked]
    for item in [*picked, *stratified(rest, CAPS["refuse"] - len(picked), survey, rng)]:
        reason = item.allowed_reasons
        scope = "out_of_scope" in reason or "no_item" in reason
        rule = {"no_period": "no_period", "missing_value": "symbol"}.get(reason[0])
        labels = TurnLabels(failure_type="classify", route="refuse", reading="new", outcome="refused",
                            refusal_reasons=list(reason), judge_rule=rule)
        add("C", "refuse", [turn(item.question, [{"name": "search_tables"}] if scope else answer_tools(item.table_key),
                                 labels, refuse=True, **({} if scope else {"table_key": item.table_key}))], item)
    for item in stratified([i for i in trends if TAIL.search(i.question)], CAPS["causal"], survey, rng):
        q = TAIL.sub("는 왜 이렇게 변했어?", item.question)
        add("C", "causal", [turn(q, answer_tools(item.table_key),
                                 TurnLabels(failure_type="classify", route="refuse", reading="new", outcome="refused",
                                            refusal_reasons=["causal"], judge_rule="causal"),
                                 refuse=True, table_key=item.table_key)])
    vals = [i for i in answerable if i.type == "value" and VALUE_TAIL.search(i.question)]
    for item in stratified(vals, CAPS["evaluative"], survey, rng):
        q = VALUE_TAIL.sub("는 높은 편이야?", item.question)
        add("C", "evaluative", [turn(q, answer_tools(item.table_key),
                                     TurnLabels(failure_type="classify", route="clarify", reading="new",
                                                outcome="clarify", judge_rule="evaluative"), table_key=item.table_key)])
    dated = [i for i in answerable if i.type == "value" and len(YEAR.findall(i.question)) == 1
             and i.required_facts[0].period and YEAR.search(i.question).group(1) == i.required_facts[0].period[:4]
             and "추계" not in survey.get(i.table_key, "")]  # T3-47: a projection's default year is debatable
    for item in stratified(dated, CAPS["default"], survey, rng):
        q = re.sub(r"\s*\d{4}년\s*(?:기준\s*)?", " ", item.question, count=1).strip()
        new = item.model_copy(update={"qid": item.qid + "-DEFAULT", "question": q, "default_applied": {"period": "latest"}})
        add("C", "default", [turn(q, answer_tools(item.table_key),
                                  answered(new, "classify").model_copy(update={"default_applied": True}),
                                  table_key=item.table_key, calc_qid=new.qid)], new)
    # missing_value from cells: the simplest symbol cell (X, -, *, …) of a table already in the set, asked as the
    # diverse builder words a refusal -> refused, not answered with a number
    from evals.builders.diverse import subject, year_text

    SYMBOLS = {"x_secret", "dash", "asterisk", "ellipsis"}
    have_symbol = sum("missing_value" in (sc["turns"][-1]["expect"]["labels"].get("refusal_reasons") or []) for sc in scenarios)
    seen_tables: set[str] = set()
    for item in values:
        if have_symbol >= CAPS["missing_value"]:
            break
        if item.table_key in seen_tables:
            continue
        seen_tables.add(item.table_key)
        symbol_cells = [c for c in cells(item.table_key).by_ref.values() if c.value_kind in SYMBOLS and len(c.period) == 4]
        if not symbol_cells:
            continue
        c = max(symbol_cells, key=lambda c: (sum(is_total_label(d.label.strip()) for d in c.dims), c.period))
        q = f"{year_text(c.period)} {josa(subject(c), '은는')}?"
        add("C", "symbol", [turn(q, answer_tools(item.table_key),
                                 TurnLabels(failure_type="classify", route="refuse", reading="new", outcome="refused",
                                            refusal_reasons=["missing_value"], judge_rule="symbol"),
                                 refuse=True, table_key=item.table_key)])
        have_symbol += 1
    rates = [i for i in paired if i.type == "trend" and unit_of(i) == "%" and TAIL.search(i.question)]
    for item in stratified(rates, CAPS["sum_rate"], survey, rng):
        q = TAIL.sub("를 다 합치면 얼마야?", item.question)
        add("C", "sum_rate", [turn(q, answer_tools(item.table_key),
                                   TurnLabels(failure_type="classify", route="refuse", reading="new", outcome="refused",
                                              refusal_reasons=["not_aggregatable"], judge_rule="not_aggregatable"),
                                   refuse=True, table_key=item.table_key)])
    # not_aggregatable also from % compares: adding two rates of one table means nothing
    for item in [i for i in compares if unit_of(i) == "%"]:
        if full("sum_rate"):
            break
        prefix, a, b, _ = compare_prefix(item)
        q = f"{prefix}, {a}{'이랑' if has_batchim(a) else '랑'} {b} 비율을 합치면 얼마야?"
        add("C", "sum_rate", [turn(q, answer_tools(item.table_key),
                                   TurnLabels(failure_type="classify", route="refuse", reading="new", outcome="refused",
                                              refusal_reasons=["not_aggregatable"], judge_rule="not_aggregatable"),
                                   refuse=True, table_key=item.table_key)])
    # partial_range: an average over a range that starts before the table does (refused, not answered with a gap)
    counts_ = [i for i in trends if unit_of(i) != "%" and TAIL.search(i.question)]
    for item in stratified(counts_, CAPS["partial_range"], survey, rng):
        first = item.required_facts[0].period[:4]
        if f"{first}년" not in item.question:
            continue
        q = TAIL.sub("의 연평균은 얼마야?", item.question.replace(f"{first}년", f"{int(first) - 5}년", 1))
        add("C", "partial_range", [turn(q, answer_tools(item.table_key),
                                        TurnLabels(failure_type="classify", route="refuse", reading="new", outcome="refused",
                                                   refusal_reasons=["no_period"], judge_rule="partial_range"),
                                        refuse=True, table_key=item.table_key)])
    # same_name: a district name several 시도 share (중구, 동구 ...) asked without its 시도 -> ask back which one
    named = [i for i in paired if i.type in ("value", "compare") and i.table_key in small]
    rng.shuffle(named)
    for item in named:
        if full("same_name"):
            break
        src = cells(item.table_key).by_ref.get(item.required_facts[0].source_ref)
        if src is None or names_sido(item.question):  # a 시도 in the question would clash with the swapped district
            continue
        for d in src.dims:
            here = d.label.strip()
            if not any(w in d.name for w in (*REGION_DIM_WORDS, "시군구", "지역")) or len(here) < 2 or here not in item.question:
                continue
            codes = defaultdict(set)
            for c in cells(item.table_key).by_ref.values():
                for x in c.dims:
                    if x.name == d.name:
                        codes[re.sub(r"\s+", "", x.label)].add(x.code)
            shared = sorted(l for l, cs in codes.items() if len(cs) > 1 and not is_total_label(l) and len(l) >= 2
                            and l not in item.question and not l.endswith(SUBTOTAL_LABELS))
            if not shared:
                continue
            q = item.question.replace(here, rng.choice(shared), 1)
            add("C", "same_name", [turn(q, answer_tools(item.table_key),
                                        TurnLabels(failure_type="classify", route="clarify", reading="new", outcome="clarify",
                                                   judge_rule="same_name"), table_key=item.table_key)])
            break
    # no_cell: a 시도 asked for a year the table holds, but not for that 시도 (세종 before 2012) -> class_mismatch
    for item in values:
        if full("no_cell"):
            break
        fact = item.required_facts[0]
        region = region_of(fact)
        year = (fact.period or "")[:4]
        if not region or not year.isdigit() or f"{year}년" not in item.question:
            continue
        surface = ("전국" if "전국" in item.question else None) if region[1] == "전국" else region_surface(item.question, region[1])
        if not surface or region[1] == "세종특별자치시":
            continue
        for old in ("2005", "2008", "2010"):
            if old >= year or cells(item.table_key).swap(fact.source_ref, year=old) is None:
                continue  # the table must hold that year for the asked 시도's neighbours
            if cells(item.table_key).swap(fact.source_ref, region="세종특별자치시") is None:
                break  # no 세종 in this table at all: it would be no_item, not a missing combination
            if cell_at(cells(item.table_key), fact.source_ref, region="세종특별자치시", year=old) is not None:
                continue  # the cell exists (a number or a symbol such as '-'): not a missing combination (T3-47 review)
            q = item.question.replace(f"{year}년", f"{old}년", 1).replace(surface, "세종특별자치시", 1)
            add("C", "no_cell", [turn(q, answer_tools(item.table_key),
                                      TurnLabels(failure_type="classify", route="refuse", reading="new", outcome="refused",
                                                 refusal_reasons=["class_mismatch"], judge_rule="no_cell"),
                                      refuse=True, table_key=item.table_key)])
            break

    # split (T3-47): a follow-up worded like a rule-tuning template goes to dev; one in DEV_SHARE by sid hash too
    shapes = tuned_shapes()
    for s in scenarios:
        leaked = any(i and tuned(t["question"], shapes) for i, t in enumerate(s["turns"]))
        if leaked or int(sha256_text(s["sid"])[:8], 16) % DEV_SHARE == 0:
            s["split"] = "dev"
    rows = [AgentScenario.model_validate(s) for s in scenarios]
    ftype = Counter(t.expect.labels.failure_type for s in rows for t in s.turns[-1:])
    info = {"scenarios": len(rows), "turns": sum(len(s.turns) for s in rows), "by_failure_type": dict(ftype),
            "by_kind": dict(count), "splits": dict(Counter(s.split for s in rows)),
            "tables": len({t.expect.table_key for s in rows for t in s.turns if t.expect.table_key}),
            "surveys": len({survey.get(t.expect.table_key) for s in rows for t in s.turns if t.expect.table_key}),
            "calc_items": len(calc), "dropped": {k: len(v) for k, v in dropped.items()}, "dropped_ids": dropped,
            "seed": seed, "wide_sha256": sha256(WIDE), "diverse_sha256": sha256(DIVERSE)}
    return rows, list(calc.values()), info


CACHE = DATASETS / "e2e_golden.rule_pred.jsonl"  # one line per finished scenario: a force-quit build resumes here
SCENARIO_TIMEOUT = 180  # seconds per scenario before its rule_pred is recorded as a timeout


class ScenarioTimeout(Exception):
    pass


def _key(s: AgentScenario) -> str:
    """A cached prediction is reused only for the same scenario: sid and every turn's question and gold table."""
    return sha256_text(s.sid + "".join(f"|{t.question}|{t.expect.table_key}" for t in s.turns))


def sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def load_cache(path: Path = CACHE) -> dict[str, list[dict]]:
    if not path.is_file():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:  # a line cut by a force-quit is just recomputed
            d = json.loads(line)
            out[d["key"]] = d["preds"]
        except (json.JSONDecodeError, KeyError):
            continue
    return out


def with_rule_pred(rows: list[AgentScenario], *, max_rows: int | None = None, timeout: int = SCENARIO_TIMEOUT,
                   cache_path: Path = CACHE, resources_of=None, parse=None) -> list[AgentScenario]:
    """Each turn's rule_pred: today's rules offline on the gold table (evals.evaluators.labels.rule_labels).

    Guards (2026-11-20, a build was force-quit while phase 1 loaded multi-million-row tables): a table over max_rows is
    not loaded (rule_pred judge_rule "skipped: large table"), a scenario over `timeout` seconds is recorded as
    "error: timeout", and every finished scenario is appended to cache_path at once, so a rerun resumes."""
    import signal

    from evals.evaluators.labels import MAX_TABLE_ROWS, TableTooLarge, rule_labels, table_resources

    if parse is None:
        from kosis_agent.envfile import load_env_file
        from kosis_rag.parse.node import parse_query
        from evals.tasks.common import load_sigungu

        load_env_file()
        sigungu = load_sigungu()
        parse = lambda q: parse_query(q, sigungu=sigungu)  # noqa: E731 - the parser the service graph uses
    resources_of = resources_of or table_resources(ROOT, max_rows or MAX_TABLE_ROWS)
    cached = load_cache(cache_path)

    def on_alarm(signum, frame):
        raise ScenarioTimeout

    alarm = timeout > 0 and hasattr(signal, "SIGALRM")
    previous = signal.signal(signal.SIGALRM, on_alarm) if alarm else None
    out: list[AgentScenario | None] = [None] * len(rows)
    # by table, so the small table cache loads each table once; the output keeps the build order
    order = sorted(range(len(rows)), key=lambda i: str(rows[i].turns[-1].expect.table_key))
    done = sum(_key(s) in cached for s in rows)
    if done:
        print(f"rule_pred resume: {done}/{len(rows)} from {cache_path.name}", flush=True)
    try:
        with cache_path.open("a", encoding="utf-8") as sink:
            for n, i in enumerate(order):
                s, key = rows[i], _key(rows[i])
                if key in cached:
                    preds = [TurnLabels.model_validate(p) for p in cached[key]]
                else:
                    if alarm:
                        signal.alarm(timeout)
                    try:
                        preds = rule_labels(s.turns, parse, resources_of)
                    except TableTooLarge as e:
                        preds = [TurnLabels(judge_rule=f"skipped: large table ({e})")] * len(s.turns)
                    except ScenarioTimeout:
                        preds = [TurnLabels(judge_rule=f"error: timeout {timeout}s")] * len(s.turns)
                    except MemoryError:
                        preds = [TurnLabels(judge_rule="error: MemoryError")] * len(s.turns)
                    except Exception as e:  # noqa: BLE001 - a rule crash is a prediction too (recorded, not raised)
                        preds = [TurnLabels(judge_rule=f"error: {type(e).__name__}")] * len(s.turns)
                    finally:
                        if alarm:
                            signal.alarm(0)
                    sink.write(json.dumps({"key": key, "sid": s.sid, "preds": [p.model_dump(exclude_none=True) for p in preds]},
                                          ensure_ascii=False) + "\n")
                    sink.flush()
                turns = [t.model_copy(update={"expect": t.expect.model_copy(update={"rule_pred": p})})
                         for t, p in zip(s.turns, preds)]
                out[i] = s.model_copy(update={"turns": turns})
                if n % 50 == 0:
                    print(f"rule_pred {n}/{len(rows)}", flush=True)
    finally:
        if alarm:
            signal.signal(signal.SIGALRM, previous)
    return out


NEW_KINDS = {"share", "extreme", "same_name", "XE", "XY", "partial_range", "no_cell", "symbol"}  # T3-47: gold by construction, checked
REVIEW = DATASETS / "e2e_golden.review.csv"
KEY_LABELS = ("outcome", "judge_rule", "qtype", "reading", "formula")


def mark_review(rows: list[AgentScenario]) -> list[AgentScenario]:
    """A new-type turn whose rule prediction disagrees with its constructed gold on a key label waits for a person
    (review = needs_review). Keeping only agreeing items would bend the golden set toward what the rules already do."""
    from evals.evaluators.labels import grade

    out = []
    for s in rows:
        if s.notes not in NEW_KINDS:
            out.append(s)
            continue
        turns = []
        for t in s.turns:
            e = t.expect
            if e.labels and e.rule_pred and not (e.rule_pred.judge_rule or "").startswith(("skipped", "error: timeout")):
                hits = grade(e.labels, e.rule_pred)
                if any(not hits.get(f, True) for f in KEY_LABELS):
                    e = e.model_copy(update={"labels": e.labels.model_copy(update={"review": "needs_review"})})
            turns.append(t.model_copy(update={"expect": e}))
        out.append(s.model_copy(update={"turns": turns}))
    return out


VERDICT = "verdict(ok/wrong)"
REVIEW_COLUMNS = ["sid", "kind", "split", "conversation", "question", "gold", "rule_pred", "review", VERDICT, "note"]


def conversation(s: AgentScenario) -> str:
    return " || ".join(t.question for t in s.turns)


def review_key(kind: str, conv: str) -> tuple[str, str]:
    from evals.golden_gate import norm

    return kind, norm(conv)


def load_reviews(path: Path = REVIEW) -> dict[tuple[str, str], dict]:
    """A person's verdicts in the review CSV, keyed by (kind, the whole conversation): they survive a rebuild."""
    import csv

    if not path.exists():
        return {}
    out = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if (r.get(VERDICT) or "").strip() in ("ok", "wrong"):
                out[review_key(r["kind"], r.get("conversation") or r["question"])] = r
    return out


def apply_reviews(rows: list[AgentScenario], reviews: dict) -> tuple[list[AgentScenario], list[dict]]:
    """ok -> the gold stands (review = accepted); wrong -> the scenario leaves the golden set (listed in meta)."""
    kept, rejected = [], []
    for s in rows:
        r = reviews.get(review_key(s.notes, conversation(s)))
        if r is None:
            kept.append(s)
        elif r[VERDICT].strip() == "wrong":
            rejected.append({"sid": s.sid, "kind": s.notes, "conversation": conversation(s), "note": r.get("note", "")})
        else:  # ok: the flagged turns (any turn of the conversation) and the graded last turn are accepted
            turns = [t.model_copy(update={"expect": t.expect.model_copy(update={"labels": t.expect.labels.model_copy(
                         update={"review": "accepted"})})}) if t.expect.labels and (i == len(s.turns) - 1 or t.expect.labels.review)
                     else t for i, t in enumerate(s.turns)]
            kept.append(s.model_copy(update={"turns": turns}))
    return kept, rejected


def write_review(rows: list[AgentScenario], reviews: dict | None = None, path: Path = REVIEW) -> int:
    """needs_review turns, every 5th other new-type turn and every turn a person already judged (verdict and note
    kept); judged scenarios that no longer exist stay at the end, so a person's work is never lost."""
    import csv

    reviews = reviews or {}
    picked = [s for s in rows if s.notes in NEW_KINDS]
    flagged = lambda s: any(t.expect.labels and t.expect.labels.review == "needs_review" for t in s.turns)  # noqa: E731
    picked = [s for n, s in enumerate(picked) if flagged(s) or n % 5 == 0 or review_key(s.notes, conversation(s)) in reviews]
    present = set()
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS)
        w.writeheader()
        for s in picked:
            # the turn a person should look at: the flagged one (a middle turn of a 3-turn conversation too), else the last
            t = next((t for t in s.turns if t.expect.labels and t.expect.labels.review == "needs_review"), s.turns[-1])
            key = review_key(s.notes, conversation(s))
            present.add(key)
            old = reviews.get(key, {})
            gold = {k: v for k, v in t.expect.labels.model_dump(exclude_none=True).items() if k in (*KEY_LABELS, "refusal_reasons")}
            pred = {k: v for k, v in (t.expect.rule_pred.model_dump(exclude_none=True) if t.expect.rule_pred else {}).items()
                    if k in (*KEY_LABELS, "refusal_reasons")}
            w.writerow({"sid": s.sid, "kind": s.notes, "split": s.split, "conversation": conversation(s), "question": t.question,
                        "gold": json.dumps(gold, ensure_ascii=False), "rule_pred": json.dumps(pred, ensure_ascii=False),
                        "review": t.expect.labels.review or "", VERDICT: old.get(VERDICT, ""), "note": old.get("note", "")})
        for key, old in reviews.items():
            if key not in present:  # rejected (left the set) or no longer generated after a rule fix
                w.writerow({**{c: old.get(c, "") for c in REVIEW_COLUMNS}, "review": "removed"})
    return len(picked)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=46)
    parser.add_argument("--no-rules", action="store_true", help="skip rule_pred (offline phase 1 rules per table)")
    parser.add_argument("--max-rows", type=int, help="rule_pred: tables over this many rows are skipped (default 2,000,000)")
    parser.add_argument("--timeout", type=int, default=SCENARIO_TIMEOUT, help="rule_pred: seconds per scenario (0 = none)")
    parser.add_argument("--fresh", action="store_true", help="rule_pred: ignore the resume cache (after a rule change)")
    parser.add_argument("--allow-gate-fail", action="store_true", help="write the files even when a blocking gate check fails (exit 0)")
    args = parser.parse_args(argv)
    rows, calc, info = build(args.seed)
    if not args.no_rules:
        if args.fresh:
            CACHE.unlink(missing_ok=True)
        rows = with_rule_pred(rows, max_rows=args.max_rows, timeout=args.timeout)
        skipped = Counter(t.expect.rule_pred.judge_rule.split(":")[0] for s in rows for t in s.turns
                          if t.expect.rule_pred and (t.expect.rule_pred.judge_rule or "").startswith(("skipped", "error")))
        info["rule_pred_not_run"] = dict(skipped)  # skipped (large table) / error (timeout, crash) turns
        rows = mark_review(rows)
        reviews = load_reviews()
        rows, rejected = apply_reviews(rows, reviews)
        info["review"] = {"judged": len(reviews), "accepted": sum(s.turns[-1].expect.labels.review == "accepted" for s in rows),
                          "rejected": len(rejected), "rejected_sids": [r["sid"] for r in rejected]}
        info["review_rows"] = write_review(rows, reviews)
    OUT.write_text("".join(s.model_dump_json(exclude_defaults=True) + "\n" for s in rows), encoding="utf-8")
    OUT_CALC.write_text("".join(i.model_dump_json(exclude_defaults=True) + "\n" for i in calc), encoding="utf-8")
    info |= {"rule_pred": not args.no_rules, "sha256": sha256(OUT), "calc_sha256": sha256(OUT_CALC)}
    # T3-47 quality gate on what was just written (plans/golden-quality-gate.md): blocking checks fail the build
    from evals import golden_gate

    gate = golden_gate.run(OUT, OUT_CALC)
    print(golden_gate.table(gate))
    golden_gate.write(gate)
    info["gate"] = {"passed": gate["passed"], "blocked": gate["blocked"],
                    "checks": {c["name"]: {"ok": c["ok"], "value": c["value"]} for c in gate["checks"]}}
    OUT.with_suffix(".meta.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in info.items() if k not in ("dropped_ids", "gate")}, ensure_ascii=False))
    if not gate["passed"]:
        print(f"quality gate blocked: {', '.join(gate['blocked'])} (-> {golden_gate.OUT})")
        return 0 if args.allow_gate_fail else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
