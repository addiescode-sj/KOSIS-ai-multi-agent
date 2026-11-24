"""KOS-192 Q4 + Q5 (plan 07): the conditional rewrite chosen after the fact. No model call.

    set PYTHONPATH=.;src;vendor\\phase2\\src;vendor\\phase1;vendor\\phase1\\src
    python scripts\\kos192\\trigger_select.py --baseline-run evals\\reports\\<B1 run> [--build v3-condyears-p2] [--stage q5]
        [--tau 0.05] [--seed 192]

Inputs: one rw_eval.py --rewrite on run made without --rewrite-tau ("always": every question rewritten; the stage folder
data/lab/rewrite/<build>/rw_eval/<stage>, or --on-run <folder>; its rows of the summary's config_ids, last per qid)
and a baseline: an evals.run_eval L1 run folder (B1, graph:staged-gpt-condyears) or a rw_eval --rewrite off stage
folder (rw_eval.load_baseline). Only items of the golden file with a baseline row are judged.

The rule for a threshold tau (select_rows): an item triggers (the rewrite is made) when pass 1's stage 1 top score is
below tau (kos192_lib.triggered; the score is the baseline row's stage1_top_score, else the on-run row's; tau "always"
triggers every item). A triggered item whose on-run row took path B takes that row (keys, stage 2, route, latency);
a triggered item whose rewrite was not used (path A: none / rejected / error / empty) keeps the baseline row but pays
the rewrite: latency_ms + the on-run row's rewrite_ms; every other item takes the baseline row unchanged.

Q4, on dev only: the candidates are the taus that trigger 20 / 35 / 50 % of the dev items with a score
(kos192_lib.trigger_threshold over TRIGGER_RATES) and "always". Each is reported with dev Hit@1, Hit@5, MRR, no-answer
F1 (graph route), trigger rate, path B rate, latency p50 and its rises against the baseline, and why it was rejected
(rejections). Plan Q4 ("효과 있는 가장 낮은 비율"): the chosen tau is the candidate with the lowest dev trigger rate
whose dev MRR is above the dev baseline's and which keeps both latency limits of (d) on dev; when none qualifies, the
result is "적합한 τ 없음" and test is not judged. --tau forces a value instead (judged on test, marked forced).

Q5, the chosen tau applied to test once, judged against the baseline over the same items:
  (a) MRR over the answerable items: the paired bootstrap over items (BOOTSTRAP_RESAMPLES resamples, fixed --seed) of
      the mean rr difference; passes when the 95 % CI's lower bound is above 0;
  (b) S0 (meta style) Hit@1: net drop (baseline hits - selected hits) of at most MAX_S0_HIT1_DROP items;
  (c) no-answer F1 drop of at most MAX_F1_DROP;
  (d) latency p50: overall rise of at most MAX_P50_RISE_MS and, over the triggered items, rise of at most
      MAX_TRIGGERED_P50_RISE_MS against the same items in the baseline.
Also Hit@1 / Hit@5 / MRR per style, trigger rate, Hit@1 up / down, rank better / worse, no-answer items answered and
answerable items refused (baseline -> selected), and the MRR with rr = 0 for refused answerable items (sensitivity).
A criterion that cannot be measured (no item) is "판정 불가" and fails the whole.

Coverage (coverage, per split): golden items, items judged (with a baseline row), items without a baseline row or an
on-run row, on-run stage 2 errors (rows the on-run made itself: path B and A-fallback; an A-baseline row repeats the
baseline's) and rewrite errors, and the baseline's own stage 2 errors (for information). Any of the first four on test
makes the whole "판정 불가" (pass None): the test result would not be the one run it should be.

Output: printed, and trigger_select.json / trigger_select.md in the on-run stage folder. Aggregates only: no qid, no
question, no rewrite, no key.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "vendor" / "phase1" / "src", ROOT / "vendor" / "phase1", ROOT / "vendor" / "phase2" / "src",
           ROOT / "src", ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
os.environ.setdefault("KOSIS_WORKSPACE", str(ROOT))

from kos192_lib import (GOLDEN, LAB, META, REWRITE_BUILDS, TRIGGER_RATES, fmt, load_golden, load_meta,  # noqa: E402
                        quantile, read_jsonl, require_file, trigger_threshold, triggered, utf8_stdout, write_json)

BUILD = REWRITE_BUILDS["v2"]  # rw_eval's default build (rewrite prompt v2)
STAGE = "q5"  # the stage name of the one --rewrite on run over all splits
ALWAYS = "always"  # the candidate that rewrites every item (tau +inf)
SEED = 192  # bootstrap seed: the same rows give the same CI
BOOTSTRAP_RESAMPLES = 10_000
CI = 0.95
BASE_STYLE = "S0"  # report.BASE_STYLE: the plain style the other styles are compared with
MAX_S0_HIT1_DROP = 2  # (b) items
MAX_F1_DROP = 0.03  # (c)
MAX_P50_RISE_MS = 1500.0  # (d) all items
MAX_TRIGGERED_P50_RISE_MS = 3000.0  # (d) triggered items vs the same items in the baseline
OUT_NAME = "trigger_select"
GATE_COUNTS = ("base_missing", "on_missing", "on_stage2_errors", "on_rewrite_errors")  # nonzero on test: no verdict
REJECT_TEXT = {"no_dev": "dev 문항 없음", "mrr": "dev MRR ≤ 기준선", "mrr_unmeasured": "MRR 측정 불가",
               "p50": f"p50 증가 > {MAX_P50_RISE_MS / 1000:g} s", "p50_unmeasured": "p50 측정 불가",
               "triggered_p50": f"트리거 p50 증가 > {MAX_TRIGGERED_P50_RISE_MS / 1000:g} s",
               "triggered_p50_unmeasured": "트리거 p50 측정 불가"}


# --- selection (pure) -----------------------------------------------------------------------------------------------

def fires(score: float | None, tau: float) -> bool:
    """The trigger rule: score < tau (kos192_lib.triggered: a missing score never triggers); tau +inf ("always")
    triggers every item, with or without a score. Pure."""
    return math.isinf(tau) and tau > 0 or triggered(score, tau)


def trigger_score(base_row: dict | None, on_row: dict | None) -> float | None:
    """Pass 1's stage 1 top score of an item: the baseline row's stage1_top_score, else the on-run row's (both are the
    highest stage 1 score of the same search); None when neither has one. Pure."""
    for row in (base_row, on_row):
        if row and row.get("stage1_top_score") is not None:
            return row["stage1_top_score"]
    return None


def select_rows(on: dict[str, dict], base: dict[str, dict], tau: float) -> list[dict]:
    """One row per baseline qid under threshold tau (module docstring): the on-run's path B row when the item triggers
    and its rewrite was used ("choice" B), the baseline row plus the on-run row's rewrite_ms in latency_ms when it
    triggers without a used rewrite ("A+rw"), else the baseline row ("A"); "triggered" set on each. Pure: the input
    rows are not changed."""
    out = []
    for qid, b in base.items():
        o = on.get(qid)
        hit = o is not None and fires(trigger_score(b, o), tau)
        if hit and o.get("path") == "B":
            row = {**o, "choice": "B"}
        elif hit:
            extra = o.get("rewrite_ms") or 0.0
            latency = b.get("latency_ms")
            row = {**b, "choice": "A+rw", "latency_ms": latency + extra if latency is not None else None}
        else:
            row = {**b, "choice": "A"}
        out.append({**row, "qid": qid, "triggered": hit})
    return out


def candidates(dev_scores: list[float], rates: tuple[float, ...] = TRIGGER_RATES) -> list[dict]:
    """The Q4 candidates: {label, target_rate, tau} for each rate (trigger_threshold over the dev scores; a rate
    without a threshold is left out) and "always" (tau +inf, target 1.0). Pure."""
    out = []
    for rate in rates:
        tau = trigger_threshold(dev_scores, rate)
        if tau is not None:
            out.append({"label": f"{rate:.0%}", "target_rate": rate, "tau": tau})
    out.append({"label": ALWAYS, "target_rate": 1.0, "tau": math.inf})
    return out


def rejections(dev: dict | None, base_mrr: float | None) -> list[str]:
    """Why a candidate is not effective on dev (REJECT_TEXT keys; [] = it qualifies, plan Q4): dev MRR not above the
    dev baseline's (to 12 digits), p50 rise over MAX_P50_RISE_MS, triggered items' p50 rise over
    MAX_TRIGGERED_P50_RISE_MS; a value that cannot be measured rejects too. dev = dev_view, None without dev items.
    Pure."""
    if dev is None:
        return ["no_dev"]
    out = []
    if dev["mrr"] is None or base_mrr is None:
        out.append("mrr_unmeasured")
    elif round(dev["mrr"], 12) <= round(base_mrr, 12):
        out.append("mrr")
    for key, limit in (("p50", MAX_P50_RISE_MS), ("triggered_p50", MAX_TRIGGERED_P50_RISE_MS)):
        rise = dev[f"{key}_rise_ms"]
        if rise is None:
            out.append(f"{key}_unmeasured")
        elif rise > limit:
            out.append(key)
    return out


def choose(evaluated: list[dict]) -> dict | None:
    """Plan Q4: among the candidates without rejections, the one with the lowest dev trigger rate (equal rates: the
    earlier candidate); None when no candidate qualifies. Each entry carries dev {trigger_rate} and rejected. Pure."""
    fit = [c for c in evaluated if not c["rejected"]]
    return min(fit, key=lambda c: c["dev"]["trigger_rate"]) if fit else None  # min keeps the first of equal rates


# --- statistics (pure) ----------------------------------------------------------------------------------------------

def paired_bootstrap(diffs: list[float], *, resamples: int = BOOTSTRAP_RESAMPLES, seed: int = SEED,
                     ci: float = CI) -> dict:
    """The paired bootstrap of the mean of per-item differences: `resamples` resamples of the items with replacement
    (random.Random(seed): the same input gives the same output), the CI = the (1-ci)/2 and (1+ci)/2 quantiles of the
    resampled means (kos192_lib.quantile). Out: n, mean, lo, hi, resamples, seed, ci (mean / lo / hi None for no
    item). Pure."""
    n = len(diffs)
    out = {"n": n, "mean": None, "lo": None, "hi": None, "resamples": resamples, "seed": seed, "ci": ci}
    if not n:
        return out
    rng = random.Random(seed)
    means = [sum(rng.choices(diffs, k=n)) / n for _ in range(resamples)]
    out.update(mean=sum(diffs) / n, lo=quantile(means, (1 - ci) / 2), hi=quantile(means, (1 + ci) / 2))
    return out


def p50(values: list[float]) -> float | None:
    """The median as report.py takes it (evals.ops.percentile); None for no value."""
    from evals.ops import percentile

    return percentile(values, 50) if values else None


def _lat(rows: list[dict]) -> list[float]:
    return [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]


def _diff(a: float | None, b: float | None) -> float | None:
    return a - b if a is not None and b is not None else None


def _mrr_refused0(graded: list[dict]) -> float | None:
    """MRR over the answerable records with rr = 0 where the graph refused (no_answer_pred). Pure."""
    ans = [g for g in graded if g["answerable"]]
    return sum(0.0 if g["no_answer_pred"] else g["rr"] for g in ans) / len(ans) if ans else None


def evaluate(selected: list[dict], base_rows: dict[str, dict], golden: dict, meta: dict[str, dict],
             split: str) -> dict:
    """Aggregates of the selected rows of one split and of the baseline over the same items. Pure: no file is read.

    Out: n, n_answerable; selected / baseline: report.group_metrics (Hit@1 / Hit@5 / MRR, no-answer P/R/F1, latency
        p50 / p95) and style (report.aggregate by style); trigger {n, rate, n_b, b_rate} (triggered items, and those
        that took path B, over the split's items); latency {p50, base_p50, triggered_p50, triggered_base_p50,
        n_triggered (triggered items with a latency)}; paired (rw_eval.paired_compare: Hit@1 up / down, rank better /
        worse, MRR change, wrong_answer, answerable_refused); mrr_refused0 {selected, baseline} (rr = 0 for refused
        answerable items); rr_diffs (selected rr - baseline rr per answerable item, in item order: for the bootstrap,
        never written).
    """
    import report
    import rw_eval

    graded, _ = report.grade_rows(selected, golden, meta, split)
    qids = {g["qid"] for g in graded}
    sel = [r for r in selected if r["qid"] in qids]
    base_graded, _ = report.grade_rows([{**base_rows[q], "qid": q} for q in base_rows if q in qids], golden, meta,
                                       split)
    by_base = {g["qid"]: g for g in base_graded}
    trig = [r for r in sel if r["triggered"]]
    trig_qids = {r["qid"] for r in trig}
    n, n_b = len(sel), sum(1 for r in trig if r["choice"] == "B")
    return {"n": n, "n_answerable": sum(1 for g in graded if g["answerable"]),
            "selected": report.group_metrics(graded), "baseline": report.group_metrics(base_graded),
            "style": report.aggregate(graded, ("style",)).get("style", {}),
            "baseline_style": report.aggregate(base_graded, ("style",)).get("style", {}),
            "trigger": {"n": len(trig), "rate": len(trig) / n if n else None, "n_b": n_b,
                        "b_rate": n_b / n if n else None},
            "latency": {"p50": p50(_lat(sel)), "base_p50": p50(_lat([base_rows[q] for q in qids])),
                        "triggered_p50": p50(_lat(trig)),
                        "triggered_base_p50": p50(_lat([base_rows[q] for q in trig_qids])),
                        "n_triggered": len(_lat(trig))},
            "paired": rw_eval.paired_compare(graded, base_graded),
            "mrr_refused0": {"selected": _mrr_refused0(graded), "baseline": _mrr_refused0(base_graded)},
            "rr_diffs": [g["rr"] - by_base[g["qid"]]["rr"] for g in graded if g["answerable"] and g["qid"] in by_base]}


def latency_rises(ev: dict) -> tuple[float | None, float | None]:
    """(p50 rise over all items, p50 rise over the triggered items against the same items in the baseline), ms, of an
    evaluation. The triggered rise is 0.0 when nothing triggers and None when triggered items carry no latency. Pure."""
    lat = ev["latency"]
    rise = _diff(lat["p50"], lat["base_p50"])
    if not ev["trigger"]["n"]:
        return rise, 0.0
    if not lat["n_triggered"]:
        return rise, None
    return rise, _diff(lat["triggered_p50"], lat["triggered_base_p50"])


def dev_view(ev: dict) -> dict:
    """The Q4 numbers of one candidate on dev: n, Hit@1, Hit@5, MRR, no-answer F1, trigger rate, path B rate, latency
    p50 and its rises (latency_rises). Pure."""
    s = ev["selected"]
    rise, trig_rise = latency_rises(ev)
    return {"n": ev["n"], "hit@1": s["hit@1"], "hit@5": s["hit@5"], "mrr": s["mrr"],
            "no_answer_f1": s["no_answer"]["f1"], "trigger_rate": ev["trigger"]["rate"],
            "b_rate": ev["trigger"]["b_rate"], "latency_p50_ms": ev["latency"]["p50"], "p50_rise_ms": rise,
            "triggered_p50_rise_ms": trig_rise}


def criteria(ev: dict, boot: dict) -> list[dict]:
    """The Q5 acceptance criteria (module docstring) of a test evaluation and its bootstrap: [{name, value, limit,
    pass}], pass None when the value cannot be measured. Pure."""
    s0 = ev["style"].get(BASE_STYLE)
    s0_base = ev["baseline_style"].get(BASE_STYLE)
    s0_drop = None
    if s0 and s0_base and s0["hit@1"] is not None and s0_base["hit@1"] is not None:
        # hit@1 is a mean over the answerable members: back to counts
        s0_drop = round(s0_base["hit@1"] * s0_base["n_answerable"] - s0["hit@1"] * s0["n_answerable"])
    f1_drop = _diff(ev["baseline"]["no_answer"]["f1"], ev["selected"]["no_answer"]["f1"])
    rise, trig_rise = latency_rises(ev)
    check = lambda v, ok: None if v is None else bool(ok(v))  # noqa: E731
    return [
        {"name": "a_mrr_ci_lo", "value": boot["lo"], "limit": 0.0, "pass": check(boot["lo"], lambda v: v > 0)},
        {"name": "b_s0_hit1_net_drop", "value": s0_drop, "limit": MAX_S0_HIT1_DROP,
         "pass": check(s0_drop, lambda v: v <= MAX_S0_HIT1_DROP)},
        {"name": "c_no_answer_f1_drop", "value": f1_drop, "limit": MAX_F1_DROP,
         "pass": check(f1_drop, lambda v: v <= MAX_F1_DROP + 1e-12)},
        {"name": "d_p50_rise_ms", "value": rise, "limit": MAX_P50_RISE_MS,
         "pass": check(rise, lambda v: v <= MAX_P50_RISE_MS)},
        {"name": "d_triggered_p50_rise_ms", "value": trig_rise, "limit": MAX_TRIGGERED_P50_RISE_MS,
         "pass": check(trig_rise, lambda v: v <= MAX_TRIGGERED_P50_RISE_MS)}]


def overall(crit: list[dict]) -> bool:
    """Every criterion passed (one that cannot be measured fails the whole). Pure."""
    return bool(crit) and all(c["pass"] is True for c in crit)


def public(ev: dict) -> dict:
    """An evaluation without its per-item rr differences (the written / rendered form). Pure."""
    return {k: v for k, v in ev.items() if k != "rr_diffs"}


def coverage(on: dict[str, dict], base: dict[str, dict], golden: dict) -> dict[str, dict]:
    """split (dev, test) -> {n_golden, n_judged (with a baseline row), base_missing, on_missing (golden items without
    a row there), on_stage2_errors (stage2_error on rows the on-run made itself: not A-baseline), on_rewrite_errors
    (rewrite_status error), base_stage2_errors (for information)}. Counts only. Pure."""
    out = {}
    for split in ("dev", "test"):
        qids = [q for q, i in golden.items() if i.split == split]
        own = [on[q] for q in qids if q in on and on[q].get("path") != "A-baseline"]
        out[split] = {"n_golden": len(qids), "n_judged": sum(1 for q in qids if q in base),
                      "base_missing": sum(1 for q in qids if q not in base),
                      "on_missing": sum(1 for q in qids if q not in on),
                      "on_stage2_errors": sum(1 for r in own if r.get("stage2_error")),
                      "on_rewrite_errors": sum(1 for q in qids if q in on and on[q].get("rewrite_status") == "error"),
                      "base_stage2_errors": sum(1 for q in qids if q in base and base[q].get("stage2_error"))}
    return out


def blocked(cov: dict[str, dict]) -> list[str]:
    """The GATE_COUNTS that are nonzero on test: any makes the test verdict "판정 불가". Pure."""
    return [k for k in GATE_COUNTS if cov.get("test", {}).get(k)]


# --- report (pure) --------------------------------------------------------------------------------------------------

CRITERIA_TEXT = {"a_mrr_ci_lo": "(a) 답 있음 MRR 차이 95% CI 하한 > 0",
                 "b_s0_hit1_net_drop": f"(b) S0 Hit@1 순감 ≤ {MAX_S0_HIT1_DROP}문항",
                 "c_no_answer_f1_drop": f"(c) 답 없음 F1 하락 ≤ {MAX_F1_DROP:.2f}",
                 "d_p50_rise_ms": f"(d) 전체 p50 증가 ≤ {MAX_P50_RISE_MS:,.0f} ms",
                 "d_triggered_p50_rise_ms": f"(d) 트리거 문항 p50 증가 ≤ {MAX_TRIGGERED_P50_RISE_MS:,.0f} ms"}
COVERAGE_TEXT = (("n_golden", "골든"), ("n_judged", "판정"), ("base_missing", "기준선 없음"),
                 ("on_missing", "on-run 없음"), ("on_stage2_errors", "on-run 2차 오류"),
                 ("on_rewrite_errors", "재작성 오류"), ("base_stage2_errors", "기준선 2차 오류(참고)"))


def verdict(ok: bool | None) -> str:
    return "판정 불가" if ok is None else ("통과" if ok else "미달")


def tau_text(tau: float | None) -> str:
    return ALWAYS if tau is None or math.isinf(tau) else f"{tau:.4f}"


def _dev_cells(d: dict | None) -> str:
    if d is None:
        return " | ".join(["-"] * 10)
    return " | ".join([fmt(d["trigger_rate"]), fmt(d["b_rate"]), fmt(d["hit@1"]), fmt(d["hit@5"]), fmt(d["mrr"]),
                       fmt(d["no_answer_f1"]), fmt(d["latency_p50_ms"], 0), fmt(d["p50_rise_ms"], 0),
                       fmt(d["triggered_p50_rise_ms"], 0), "-"])


def render(out: dict) -> str:
    """The markdown report of trigger_select.json (aggregates only). One H1. Pure."""
    lines = [f"# KOS-192 trigger_select — {out['on_run']} vs {out['baseline_run']}", "",
             f"- 규칙: 1차 stage 1 최고 점수 < τ 이면 재작성; 재작성이 쓰이면(경로 B) 그 행, 아니면 기준선 행"
             f" (+ 재작성 시간). 부트스트랩 {out['resamples']:,}회, seed {out['seed']}",
             "", "## 문항 수 확인", "", "| split | " + " | ".join(t for _, t in COVERAGE_TEXT) + " |",
             "|---|" + "---|" * len(COVERAGE_TEXT)]
    for split, c in out["coverage"].items():
        lines.append(f"| {split} | " + " | ".join(str(c[k]) for k, _ in COVERAGE_TEXT) + " |")
    lines += ["", "## Q4 dev 후보 (효과 있는 가장 낮은 비율: dev MRR > 기준선, 지연 기준 (d) 충족)", "",
              "| 후보 | τ | 트리거율 | 경로 B | Hit@1 | Hit@5 | MRR | 답 없음 F1 | p50 ms | p50 증가 | 트리거 p50 증가 "
              "| 탈락 사유 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    if out["baseline_dev"]:
        lines.append(f"| 기준선 | - | {_dev_cells(out['baseline_dev'])} |")
    chosen = out["chosen"]
    for c in out["candidates"]:
        mark = " ✓" if chosen and c["label"] == chosen["label"] else ""
        why = ", ".join(REJECT_TEXT[r] for r in c["rejected"]) or "적합"
        cells = _dev_cells(c["dev"]).rsplit(" | ", 1)[0]
        lines.append(f"| {c['label']}{mark} | {tau_text(c['tau'])} | {cells} | {why} |")
    if chosen is None:
        lines += ["", "- 선택: 적합한 τ 없음 (dev MRR이 기준선보다 높고 지연 기준을 지키는 후보가 없음): test 판정하지 않음"
                  " (--tau 로 지정하면 판정)"]
        return "\n".join(lines) + "\n"
    lines += ["", f"- 선택: {chosen['label']} (τ {tau_text(chosen['tau'])})"
              + (" — --tau 지정 (dev 규칙으로 고른 값이 아님)" if chosen["forced"] else "")]
    t = out["test"]
    if t is None:
        lines += ["", "## Q5 test 판정", "", "- test 문항이 없음 (on-run 또는 기준선에 test 행 없음): 판정하지 않음"]
        return "\n".join(lines) + "\n"
    s, bs, p, bt = t["selected"], t["baseline"], t["paired"], out["bootstrap"]
    w, ar, m0 = p["wrong_answer"], p["answerable_refused"], t["mrr_refused0"]
    lines += ["", f"## Q5 test 판정 (τ {tau_text(chosen['tau'])}, n {t['n']}, 답 있음 {t['n_answerable']})", "",
              "| | Hit@1 | Hit@5 | MRR | 답 없음 F1 | p50 ms |", "|---|---|---|---|---|---|",
              f"| 기준선 | {fmt(bs['hit@1'])} | {fmt(bs['hit@5'])} | {fmt(bs['mrr'])} | {fmt(bs['no_answer']['f1'])} | "
              f"{fmt(t['latency']['base_p50'], 0)} |",
              f"| 선택 | {fmt(s['hit@1'])} | {fmt(s['hit@5'])} | {fmt(s['mrr'])} | {fmt(s['no_answer']['f1'])} | "
              f"{fmt(t['latency']['p50'], 0)} |", "",
              f"- 트리거 {t['trigger']['n']}/{t['n']} ({fmt(t['trigger']['rate'])}), 그중 경로 B {t['trigger']['n_b']}; "
              f"트리거 문항 p50 {fmt(t['latency']['triggered_base_p50'], 0)} → {fmt(t['latency']['triggered_p50'], 0)} ms",
              f"- Hit@1 {p['hit1']['base']} → {p['hit1']['rw']} (오름 {p['hit1']['up']} / 내림 {p['hit1']['down']}), "
              f"순위 나아짐 {p['rank']['better']} / 나빠짐 {p['rank']['worse']}",
              f"- 답 없음 잘못 답함 {w['base']} → {w['rw']} (고침 {w['fixed']} / 새로 틀림 {w['broken']}), "
              f"답 있음 거절 {ar['base']} → {ar['rw']}",
              f"- MRR 차이 {fmt(bt['mean'], 4)}, 95% CI [{fmt(bt['lo'], 4)}, {fmt(bt['hi'], 4)}] (n {bt['n']})",
              f"- 민감도: 거절한 답 있음 문항을 rr 0으로 둔 MRR {fmt(m0['baseline'])} → {fmt(m0['selected'])}",
              "", "### 스타일별", "", "| 스타일 | n 답 있음 | Hit@1 기준→선택 | Hit@5 기준→선택 | MRR 기준→선택 |",
              "|---|---|---|---|---|"]
    for name, g in t["style"].items():
        b0 = t["baseline_style"].get(name, {})
        lines.append(f"| {name} | {g['n_answerable']} | {fmt(b0.get('hit@1'))} → {fmt(g['hit@1'])} | "
                     f"{fmt(b0.get('hit@5'))} → {fmt(g['hit@5'])} | {fmt(b0.get('mrr'))} → {fmt(g['mrr'])} |")
    lines += ["", "### 기준", ""]
    for c in out["criteria"]:
        value = fmt(c["value"], 0 if c["name"].startswith("d_") else 4) if isinstance(c["value"], float) else c["value"]
        lines.append(f"- {CRITERIA_TEXT[c['name']]}: {value if value is not None else '-'} → {verdict(c['pass'])}")
    gate = "" if not out["blocked"] else (" (test 누락·오류: " + ", ".join(
        f"{dict(COVERAGE_TEXT)[k]} {out['coverage']['test'][k]}" for k in out["blocked"]) + ")")
    lines += ["", f"- 종합: {verdict(out['pass'])}{gate}"]
    return "\n".join(lines) + "\n"


# --- inputs ---------------------------------------------------------------------------------------------------------

def load_on_run(folder: Path) -> tuple[dict[str, dict], dict]:
    """(qid -> row, summary) of an rw_eval --rewrite on run without --rewrite-tau: the last row per qid of the
    summary's config ids (rw_eval.summary_ids: the id and the legacy id). SystemExit (one line) when the folder is not
    one. Side effects: reads the files."""
    import rw_eval

    require_file(folder / "summary.json", "summary.json of the rw_eval stage folder (--rewrite on, no --rewrite-tau)")
    require_file(folder / "results.jsonl", "results.jsonl of the rw_eval stage folder")
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8-sig"))
    config = summary.get("config") or {}
    if config.get("rewrite", "on") != "on" or config.get("rewrite_tau") is not None:
        raise SystemExit(f"{folder.name}: trigger_select needs a rw_eval --rewrite on run without --rewrite-tau "
                         f"(rewrite {config.get('rewrite')}, tau {config.get('rewrite_tau')})")
    return rw_eval.current_rows(read_jsonl(folder / "results.jsonl"), rw_eval.summary_ids(summary)), summary


def load_base(folder: Path) -> dict[str, dict]:
    """qid -> baseline row (rw_eval.load_baseline: an L1 run folder or an rw_eval --rewrite off stage folder; the
    last row per qid). SystemExit when the folder is an rw_eval --rewrite on run. Side effects: reads the files."""
    import rw_eval

    summary = folder / "summary.json"
    if summary.is_file():
        config = json.loads(summary.read_text(encoding="utf-8-sig")).get("config") or {}
        if config.get("rewrite") == "on":
            raise SystemExit(f"{folder.name}: the baseline must be an L1 run or a rw_eval --rewrite off stage folder")
    return {r["qid"]: r for r in rw_eval.load_baseline(folder)}


def select_and_judge(on: dict[str, dict], base: dict[str, dict], golden: dict, meta: dict[str, dict], *,
                     tau: float | None = None, seed: int = SEED, resamples: int = BOOTSTRAP_RESAMPLES,
                     rates: tuple[float, ...] = TRIGGER_RATES) -> dict:
    """Q4 and Q5 over the rows (module docstring): coverage, the dev candidates with their rejections and the choice
    (tau forces one), then the test judgement unless no candidate qualifies. Out: the trigger_select.json body without
    the run names (aggregates only): seed, resamples, rates, n_scored_dev, coverage, candidates, baseline_dev, chosen
    (None: 적합한 τ 없음), test, bootstrap, criteria, blocked (GATE_COUNTS nonzero on test), pass (None when test is
    not judged or blocked; else overall over the criteria). Pure: no file is read."""
    split_of = {q: i.split for q, i in golden.items()}
    base = {q: r for q, r in base.items() if q in split_of}
    dev_base = {q: r for q, r in base.items() if split_of[q] == "dev"}
    test_base = {q: r for q, r in base.items() if split_of[q] == "test"}
    if tau is None and not dev_base:
        raise SystemExit("no dev item with a baseline row: tau cannot be chosen (pass --tau)")
    scores = [s for q, r in dev_base.items() if (s := trigger_score(r, on.get(q))) is not None]
    cands = candidates(scores, rates)
    if tau is not None:
        cands.append({"label": "--tau", "target_rate": None, "tau": tau})
    baseline_dev = dev_view(evaluate(select_rows({}, dev_base, math.inf), dev_base, golden, meta, "dev")) \
        if dev_base else None
    base_mrr = baseline_dev["mrr"] if baseline_dev else None
    evaluated = []
    for c in cands:
        dev = dev_view(evaluate(select_rows(on, dev_base, c["tau"]), dev_base, golden, meta, "dev")) \
            if dev_base else None
        evaluated.append({**c, "dev": dev, "rejected": rejections(dev, base_mrr)})
    pick = evaluated[-1] if tau is not None else choose(evaluated)
    cov = coverage(on, base, golden)
    out = {"seed": seed, "resamples": resamples, "rates": list(rates), "n_scored_dev": len(scores), "coverage": cov,
           "candidates": evaluated, "baseline_dev": baseline_dev,
           "chosen": {"label": pick["label"], "tau": pick["tau"], "forced": tau is not None} if pick else None,
           "test": None, "bootstrap": None, "criteria": [], "blocked": blocked(cov), "pass": None}
    if pick and test_base:
        ev = evaluate(select_rows(on, test_base, pick["tau"]), test_base, golden, meta, "test")
        boot = paired_bootstrap(ev["rr_diffs"], resamples=resamples, seed=seed)
        crit = criteria(ev, boot)
        out.update(test=public(ev), bootstrap=boot, criteria=crit)
        out["pass"] = None if out["blocked"] else overall(crit)
    return out


# --- main -----------------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    parser = argparse.ArgumentParser(description="KOS-192 Q4 + Q5: tau on dev, applied once to test (no model call).")
    parser.add_argument("--baseline-run", type=Path, required=True,
                        help="an evals.run_eval L1 run folder (B1) or a rw_eval --rewrite off stage folder")
    parser.add_argument("--build", default=BUILD, help=f"rw_eval build under data/lab/rewrite (default {BUILD})")
    parser.add_argument("--stage", default=STAGE, help=f"rw_eval stage of the --rewrite on run (default {STAGE})")
    parser.add_argument("--on-run", type=Path,
                        help="the rw_eval --rewrite on stage folder (default: data/lab/rewrite/<build>/rw_eval/<stage>)")
    parser.add_argument("--tau", type=float, help="force this tau instead of the dev choice (judged, marked forced)")
    parser.add_argument("--seed", type=int, default=SEED, help=f"bootstrap seed (default {SEED})")
    parser.add_argument("--resamples", type=int, default=BOOTSTRAP_RESAMPLES,
                        help=f"bootstrap resamples (default {BOOTSTRAP_RESAMPLES})")
    parser.add_argument("--golden", type=Path, default=GOLDEN, help="retrieval golden file (default: the KOS-192 set)")
    parser.add_argument("--meta", type=Path, default=META, help="meta file: style per qid")
    args = parser.parse_args(argv)

    folder = args.on_run or LAB / args.build / "rw_eval" / args.stage
    on, _ = load_on_run(folder)
    base = load_base(args.baseline_run)
    golden = load_golden(args.golden)
    meta = load_meta(args.meta)
    if not meta:
        print(f"note: no meta file at {args.meta}: style is '-' for every item, (b) cannot be judged", file=sys.stderr)
    out = {"on_run": folder.name, "baseline_run": args.baseline_run.resolve().name,
           **select_and_judge(on, base, golden, meta, tau=args.tau, seed=args.seed, resamples=args.resamples)}
    if out["blocked"]:
        print("note: test items missing or with errors (see 문항 수 확인): the verdict is 판정 불가", file=sys.stderr)
    text = render(out)
    write_json(folder / f"{OUT_NAME}.json", out)
    (folder / f"{OUT_NAME}.md").write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"-> {folder / (OUT_NAME + '.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
