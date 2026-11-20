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

from evals.builders.diverse import REGION_SUFFIX, SHORT as SHORT_NAMES, josa
from evals.builders.multiturn import Cells, eun, region_of, stat_call
from evals.builders.phase1 import sha256
from evals.schemas import DATASETS, ROOT, TABLES, AgentScenario, CalcItem, TurnLabels, load_jsonl

WIDE = DATASETS / "calculation_golden_wide.jsonl"
DIVERSE = DATASETS / "calculation_golden_diverse.jsonl"
OUT = DATASETS / "e2e_golden.jsonl"
OUT_CALC = DATASETS / "e2e_golden_calc.jsonl"
QUOTA = {"numbers": 250, "rewrite_mq": 20}  # the rest take every item their rule finds (capped below)
CAPS = {"ratio": 25, "cagr": 20, "E": 25, "X": 25, "Y": 15, "R": 15, "C": 15, "S": 10,
        "refuse": 50, "causal": 15, "evaluative": 15, "default": 20, "sum_rate": 10}
ELLIPSIS = ["{r}{eun}?", "그럼 {r}{eun}?", "{r} 쪽은?"]
CORRECTION = ["아니 {y}년으로", "{y}년 기준으로 다시", "{y}년{eun}?"]
RELATIVE = [("작년은?", -1), ("그 전 해는?", -1), ("재작년은?", -2)]
CHART = [("꺾은선 그래프로 보여줘", "line"), ("막대그래프로 그려줘", "bar"), ("그 표로 막대 차트 그려줘", "bar")]
REFERENCE = ["그 표에서 ", "같은 표에서 "]
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


def build(seed: int = 46) -> tuple[list[AgentScenario], list[CalcItem], dict]:
    rng = random.Random(seed)
    survey = surveys()
    wide = load_jsonl(WIDE, CalcItem)
    small = small_tables()
    answerable = [i for i in wide if i.kind == "answerable"]
    # the wide set holds number answers only, one per table: refusals and same-table pairs (reference turns) come from
    # the diverse set too (tables with a Parquet file only)
    have = {i.qid for i in wide}
    extra = [i for i in load_jsonl(DIVERSE, CalcItem) if i.qid not in have
             and (ROOT / "data" / "values" / f"{i.table_key}.parquet").is_file()]
    refusals = [i for i in [*wide, *extra] if i.kind == "not_answerable"]
    paired = answerable + [i for i in extra if i.kind == "answerable"]
    scenarios: list[dict] = []
    calc: dict[str, CalcItem] = {}
    count: Counter = Counter()

    def add(prefix: str, kind: str, turns: list[dict], *items: CalcItem) -> None:
        count[kind] += 1
        scenarios.append({"sid": f"E2E-{prefix}-{kind}-{count[kind]:03d}", "split": "test", "source": kind,
                          "notes": kind, "turns": turns})
        for i in items:
            calc[i.qid] = i

    def full(kind: str) -> bool:
        return count[kind] >= CAPS.get(kind, 10**9)

    # ① numbers: value and compare as written, round-robin over surveys
    plain = [i for i in answerable if i.type in ("value", "compare")]
    for item in stratified(plain, QUOTA["numbers"], survey, rng):
        add("N", item.type, [turn(item.question, answer_tools(item.table_key), answered(item, "numbers"),
                                  table_key=item.table_key, calc_qid=item.qid)], item)

    # ② formula: every trend (the % / count trap), "몇 배" from compares, "연평균 증가율" from count trends
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

    # ③ rewrite: follow-up turns on one-fact value questions (answers are swapped cells read by code)
    values = [i for i in answerable if i.type == "value" and len(i.required_facts) == 1 and i.table_key in small]
    rng.shuffle(values)
    cells_cache: dict[str, Cells] = {}

    def cells(key: str) -> Cells:
        if key not in cells_cache:
            cells_cache[key] = Cells(key)
        return cells_cache[key]

    def follow(item: CalcItem, kind: str, q2: str, resolved: str, cell, extra_labels: dict | None = None) -> None:
        new = CalcItem(qid=f"{item.qid}-{kind}-{cell.ref().split('|', 2)[2].replace('|', '-')}", kind="answerable",
                       type="value", question=resolved, table_key=item.table_key, table_sha256=item.table_sha256,
                       required_facts=[item.required_facts[0].model_copy(update={
                           "value": cell.value, "period": cell.period, "source_ref": cell.ref(),
                           "dims": {**(item.required_facts[0].dims or {}), **{d.name: d.label for d in cell.dims}}})],
                       eda_traps=["synthetic_multiturn"])
        first = turn(item.question, answer_tools(item.table_key), answered(item, "rewrite"),
                     table_key=item.table_key, calc_qid=item.qid)
        labels = TurnLabels(failure_type="rewrite", route="rewrite", reading="carry_over", resolved_question=resolved,
                            outcome="answered", qtype="value", **(extra_labels or {}))
        second = turn(q2, [stat_call(item.table_key)], labels, table_key=item.table_key, calc_qid=new.qid)
        add("R", kind, [first, second], item, new)

    for item in values:
        if full("E") and full("X") and full("Y"):
            break
        fact = item.required_facts[0]
        year = (fact.period or "")[:4]
        region = region_of(fact)
        surface = region and (("전국" if "전국" in item.question else None) if region[1] == "전국"
                              else region_surface(item.question, region[1]))
        if region and not full("E") and surface:
            targets = [r for r in SIDO if r != region[1]]
            rng.shuffle(targets)
            for target in targets:
                if (cell := cells(item.table_key).swap(fact.source_ref, region=target)) is not None:
                    short = SHORT_NAMES.get(target) or REGION_SUFFIX.sub("", target)
                    follow(item, "E", rng.choice(ELLIPSIS).format(r=short, eun=eun(short)),
                           item.question.replace(surface, target, 1), cell)
                    break
        if year.isdigit() and f"{year}년" in item.question:
            if not full("X"):
                for back in (1, 2, 3, 5):
                    target = str(int(year) - back)
                    if (cell := cells(item.table_key).swap(fact.source_ref, year=target)) is not None:
                        follow(item, "X", rng.choice(CORRECTION).format(y=target, eun=eun(target + "년")),
                               item.question.replace(f"{year}년", f"{target}년", 1), cell)
                        break
            elif not full("Y"):
                phrase, step = rng.choice(RELATIVE)
                target = str(int(year) + step)
                if (cell := cells(item.table_key).swap(fact.source_ref, year=target)) is not None:
                    follow(item, "Y", phrase, item.question.replace(f"{year}년", f"{target}년", 1), cell)

    # R reference: a compare question of the same table asked "그 표에서 …" after its value question
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
    # S topic switch: a question about another survey's table must not carry the first one's conditions
    plain_r = [i for i in plain if i.table_key in small]
    rng.shuffle(plain_r)
    for a, b in zip(plain_r[::2], plain_r[1::2]):
        if full("S"):
            break
        if survey.get(a.table_key) == survey.get(b.table_key):
            continue
        add("R", "S", [turn(a.question, answer_tools(a.table_key), answered(a, "rewrite"), table_key=a.table_key, calc_qid=a.qid),
                       turn(b.question, answer_tools(b.table_key), answered(b, "rewrite"), table_key=b.table_key, calc_qid=b.qid)], a, b)
    # MQ: two value questions of different surveys in one turn; "둘의 차이는?" when both are in one unit
    singles = [i for i in values if i.required_facts[0].value is not None]
    rng.shuffle(singles)
    made = 0
    for a, b in zip(singles[::2], singles[1::2]):
        if made >= QUOTA["rewrite_mq"]:
            break
        if survey.get(a.table_key) == survey.get(b.table_key):
            continue
        qa, qb = a.question.rstrip("?？ "), b.question.rstrip("?？ ")
        op = "diff" if unit_of(a) == unit_of(b) and made % 2 else None
        question = f"{qa}, 그리고 {qb}?" + (" 둘의 차이는?" if op else "")
        labels = TurnLabels(failure_type="rewrite", route="rewrite", reading="new", sub_questions=[qa, qb], op=op,
                            outcome="answered")
        expect = {"calc_qids": [a.qid, b.qid], "table_key": b.table_key}
        if op:
            expect["combine_value"] = round(a.required_facts[0].value - b.required_facts[0].value, 6)
        add("R", "MQ", [turn(question, [{"name": "search_tables"}, {"name": "search_tables"}, stat_call(a.table_key),
                                        stat_call(b.table_key), *([{"name": "combine_values", "args": {"op": op}}] if op else [])],
                             labels, **expect)], a, b)
        made += 1

    # ④ classify
    rng.shuffle(refusals)
    for item in stratified(refusals, CAPS["refuse"], survey, rng):
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
             and i.required_facts[0].period and YEAR.search(i.question).group(1) == i.required_facts[0].period[:4]]
    for item in stratified(dated, CAPS["default"], survey, rng):
        q = re.sub(r"\s*\d{4}년\s*(?:기준\s*)?", " ", item.question, count=1).strip()
        new = item.model_copy(update={"qid": item.qid + "-DEFAULT", "question": q, "default_applied": {"period": "latest"}})
        add("C", "default", [turn(q, answer_tools(item.table_key),
                                  answered(new, "classify").model_copy(update={"default_applied": True}),
                                  table_key=item.table_key, calc_qid=new.qid)], new)
    rates = [i for i in trends if unit_of(i) == "%" and TAIL.search(i.question)]
    for item in stratified(rates, CAPS["sum_rate"], survey, rng):
        q = TAIL.sub("를 다 합치면 얼마야?", item.question)
        add("C", "sum_rate", [turn(q, answer_tools(item.table_key),
                                   TurnLabels(failure_type="classify", route="refuse", reading="new", outcome="refused",
                                              refusal_reasons=["not_aggregatable"], judge_rule="not_aggregatable"),
                                   refuse=True, table_key=item.table_key)])

    rows = [AgentScenario.model_validate(s) for s in scenarios]
    ftype = Counter(t.expect.labels.failure_type for s in rows for t in s.turns[-1:])
    info = {"scenarios": len(rows), "turns": sum(len(s.turns) for s in rows), "by_failure_type": dict(ftype),
            "by_kind": dict(count), "tables": len({t.expect.table_key for s in rows for t in s.turns if t.expect.table_key}),
            "surveys": len({survey.get(t.expect.table_key) for s in rows for t in s.turns if t.expect.table_key}),
            "calc_items": len(calc), "seed": seed, "wide_sha256": sha256(WIDE), "diverse_sha256": sha256(DIVERSE)}
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=46)
    parser.add_argument("--no-rules", action="store_true", help="skip rule_pred (offline phase 1 rules per table)")
    parser.add_argument("--max-rows", type=int, help="rule_pred: tables over this many rows are skipped (default 2,000,000)")
    parser.add_argument("--timeout", type=int, default=SCENARIO_TIMEOUT, help="rule_pred: seconds per scenario (0 = none)")
    parser.add_argument("--fresh", action="store_true", help="rule_pred: ignore the resume cache (after a rule change)")
    args = parser.parse_args(argv)
    rows, calc, info = build(args.seed)
    if not args.no_rules:
        if args.fresh:
            CACHE.unlink(missing_ok=True)
        rows = with_rule_pred(rows, max_rows=args.max_rows, timeout=args.timeout)
        skipped = Counter(t.expect.rule_pred.judge_rule.split(":")[0] for s in rows for t in s.turns
                          if t.expect.rule_pred and (t.expect.rule_pred.judge_rule or "").startswith(("skipped", "error")))
        info["rule_pred_not_run"] = dict(skipped)  # skipped (large table) / error (timeout, crash) turns
    OUT.write_text("".join(s.model_dump_json(exclude_defaults=True) + "\n" for s in rows), encoding="utf-8")
    OUT_CALC.write_text("".join(i.model_dump_json(exclude_defaults=True) + "\n" for i in calc), encoding="utf-8")
    info |= {"rule_pred": not args.no_rules, "sha256": sha256(OUT), "calc_sha256": sha256(OUT_CALC)}
    OUT.with_suffix(".meta.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
