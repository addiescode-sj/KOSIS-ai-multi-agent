"""Before -> after verdict for two runs of the same layer and golden (plans/T3-63.md): e2e, agent, l1, l2 or l3.

    python -m evals.compare --base evals/reports/<baseline run> --after evals/reports/<after run> [--aa <baseline again>]

Questions are paired by (sid or qid, turn); each layer's question pass is PASSED below. The pass-rate difference and the per-question latency difference get the team's
paired bootstrap 95% interval (phase 2 lab_common.boot, T2-40: 10,000 resamples, seed 0). The verdict follows the rules
fixed in plans/T3-63.md before any result: guardrail (new violations), accuracy (interval vs 0), one source losing
alone, then cost and latency. Runs of different goldens, splits or layers are refused, as phase 1 eval.regress does.
Writes evals/reports/compare-<base>-vs-<after>/compare.md and compare.json (not committed, like run folders).
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

from evals.quality import COUNT_TOLERANCE
from evals.schemas import ROOT

RESAMPLES = 10_000
SEED = 0
COST_LIMIT = 0.20  # +20% p95 latency or cost per request is "costlier"
CHEAPER = -0.10  # both down by 10% or more is "cheaper"
SOURCE_LOSSES = 3  # one source with this many losses and no win holds an accuracy gain
AA_LATENCY_DRIFT = 0.20  # the same code twice: p95 latency moving more than this means the session is too noisy
SAME_KEYS = ("layer", "split")  # plus the golden hash


class Incomparable(ValueError):
    pass


PASS_RULE = {"l1": "정답 표가 1위(답할 수 없는 문항은 거절)", "l2": "슬롯 전부 맞음", "l3": "계산 판정 PASS",
             "agent": "턴 통과(툴·상태·수치)", "e2e": "턴 통과(툴·상태·수치)"}


def passed(layer: str | None, row: dict) -> bool:
    """One question's pass in each layer: l1 the right table first (or a right refusal for an unanswerable question),
    l2 every slot right, l3 the calculation verdict PASS, agent / e2e the turn's own pass (tools, state, numbers)."""
    if layer == "l1":
        return row.get("hit@1") == 1 if row.get("answerable", True) else bool(row.get("no_answer_pred"))
    if layer == "l2":
        return row.get("all_fields") == 1
    if layer == "l3":
        return row.get("verdict") == "PASS"
    return bool(row.get("passed"))


def load_run(path: Path) -> dict:
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    ops = json.loads((path / "ops.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (path / "results.jsonl").read_text(encoding="utf-8").splitlines() if line]
    by_key = {}
    for r in rows:
        if r.get("run", 0) == 0:  # pass^k runs (agent --repeat): the first run pairs with the other side's first
            by_key[(r.get("sid") or r["qid"], r.get("turn", 0))] = r | {"_pass": passed(manifest.get("layer"), r)}
    return {"path": path, "manifest": manifest, "ops": ops, "rows": by_key}


def check_comparable(base: dict, after: dict) -> None:
    """Refuse runs that do not answer the same questions in the same way."""
    a, b = base["manifest"], after["manifest"]
    golden = lambda m: (m.get("golden") or {}).get("sha256")
    problems = [f"{k}: {a.get(k)} != {b.get(k)}" for k in SAME_KEYS if a.get(k) != b.get(k)]
    if golden(a) != golden(b):
        problems.append(f"golden sha256: {golden(a)} != {golden(b)}")
    if set(base["rows"]) != set(after["rows"]):
        problems.append(f"questions differ: {len(set(base['rows']) ^ set(after['rows']))} not in both runs")
    if problems:
        raise Incomparable("; ".join(problems))


def boot(diff: list[float], resamples: int = RESAMPLES, seed: int = SEED) -> tuple[float, float]:
    """95% bootstrap interval of the mean of paired differences (phase 2 lab_common.boot without clusters)."""
    if not diff:
        return 0.0, 0.0
    rng = random.Random(seed)
    n = len(diff)
    means = sorted(sum(rng.choices(diff, k=n)) / n for _ in range(resamples))
    return means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1]


def violations(row: dict) -> list[str]:
    """What breaks a project principle in one answer: a number without a source fact, a missing source line, a crash."""
    out = []
    if row.get("unsupported_numbers"):
        out.append("unsupported_numbers")
    if row.get("missing_sources"):
        out.append("missing_sources")
    if row.get("error") or "run_failure" in (row.get("errors") or []):
        out.append("run_failure")
    return out


def _change(new: float | None, old: float | None) -> float | None:
    return None if not old or new is None else new / old - 1


def _ops(run: dict) -> dict:
    o = run["ops"]
    return {"p95_ms": (o.get("latency_ms") or {}).get("p95"), "cost": (o.get("cost") or {}).get("per_request_mean")}


def paired(base: dict, after: dict, sources: dict[str, str]) -> dict:
    keys = sorted(base["rows"])
    b, a = base["rows"], after["rows"]
    diff = [int(a[k]["_pass"]) - int(b[k]["_pass"]) for k in keys]
    source = lambda k: sources.get(k[0]) or a[k].get("qtype") or a[k].get("kind") or "?"  # noqa: E731
    item = lambda k: {"sid": k[0], "turn": k[1], "question": a[k].get("question"),
                      "source": source(k), "errors": a[k].get("errors") or []}
    wins = [item(k) for k, d in zip(keys, diff) if d > 0]
    losses = [item(k) for k, d in zip(keys, diff) if d < 0]
    lat = [a[k]["latency_ms"] - b[k]["latency_ms"] for k in keys
           if a[k].get("latency_ms") is not None and b[k].get("latency_ms") is not None]
    new_violations = [{"sid": k[0], "turn": k[1], "what": sorted(set(violations(a[k])) - set(violations(b[k])))}
                      for k in keys if set(violations(a[k])) - set(violations(b[k]))]
    by_source: dict[str, dict[str, int]] = {}
    for k, d in zip(keys, diff):
        s = by_source.setdefault(source(k), {"n": 0, "wins": 0, "losses": 0})
        s["n"] += 1
        s["wins"] += d > 0
        s["losses"] += d < 0
    ob, oa = _ops(base), _ops(after)
    return {
        "n": len(keys),
        "base_pass": sum(b[k]["_pass"] for k in keys) / len(keys),
        "after_pass": sum(a[k]["_pass"] for k in keys) / len(keys),
        "diff": sum(diff) / len(keys), "ci": boot(diff),
        "wins": wins, "losses": losses, "new_violations": new_violations, "by_source": by_source,
        "latency_diff_median_ms": statistics.median(lat) if lat else None, "latency_diff_ci_ms": boot(lat),
        "p95_ms": {"base": ob["p95_ms"], "after": oa["p95_ms"], "change": _change(oa["p95_ms"], ob["p95_ms"])},
        "cost": {"base": ob["cost"], "after": oa["cost"], "change": _change(oa["cost"], ob["cost"])},
    }


def verdict(c: dict) -> tuple[str, list[str]]:
    """(채택 | 보류 | 기각, reasons) by the rules of plans/T3-63.md."""
    why = []
    if len(c["new_violations"]) > COUNT_TOLERANCE:
        return "기각", [f"가드레일: 새로 생긴 위반 {len(c['new_violations'])}건 > 허용 {COUNT_TOLERANCE}"]
    lo, hi = c["ci"]
    accuracy = "개선" if lo > 0 else "악화" if hi < 0 else "차이 미확인"
    why.append(f"정확도 {accuracy}: 통과율 {c['diff']:+.1%} [{lo:+.1%}, {hi:+.1%}], "
               f"이김 {len(c['wins'])} · 짐 {len(c['losses'])}")
    if accuracy == "악화":
        return "기각", why
    lopsided = [s for s, v in c["by_source"].items() if v["losses"] >= SOURCE_LOSSES and v["wins"] == 0]
    if accuracy == "개선" and lopsided:
        return "보류", why + [f"원천 쏠림: {', '.join(lopsided)}에서 {SOURCE_LOSSES}패 이상·0승"]
    changes = [x for x in (c["p95_ms"]["change"], c["cost"]["change"]) if x is not None]
    costlier = any(x > COST_LIMIT for x in changes)
    cheaper = len(changes) == 2 and all(x <= CHEAPER for x in changes)
    named = [f"{name} {x:+.0%}" for name, x in (("지연 p95", c["p95_ms"]["change"]), ("요청당 비용", c["cost"]["change"]))
             if x is not None]
    why.append("비용·지연: " + (", ".join(named) or "기록 없음"))
    if accuracy == "개선":
        return "채택", why + (["비용·지연 증가를 결과에 기록"] if costlier else [])
    if cheaper:
        return "채택", why + ["같은 정확도에 더 싸고 빠름"]
    return "기각", why + ["정확도 차이가 확인되지 않아 바꿀 이유 없음"]


def aa_check(base: dict, again: dict) -> dict:
    """The same code twice: a usable session shows no accuracy difference and a steady p95 latency."""
    c = paired(base, again, {})
    lo, hi = c["ci"]
    drift = c["p95_ms"]["change"]
    ok = lo <= 0 <= hi and (drift is None or abs(drift) <= AA_LATENCY_DRIFT)
    return {"ok": ok, "flips": len(c["wins"]) + len(c["losses"]), "diff": c["diff"], "ci": c["ci"], "p95_change": drift}


def sources_of(path: Path | None) -> dict[str, str]:
    if path is None or not Path(path).exists():
        return {}
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]
    return {r["sid"]: r.get("source") or "?" for r in rows if "sid" in r}


def render(base: dict, after: dict, c: dict, decision: str, why: list[str], aa: dict | None) -> str:
    m = lambda run: run["manifest"]
    pct = lambda x: "—" if x is None else f"{x:+.0%}"
    num = lambda x, f="{:,.0f}": "—" if x is None else f.format(x)
    lines = [f"# 전→후 비교: {m(base)['run_id']} → {m(after)['run_id']}", "",
             f"## 판정: **{decision}**", *[f"- {w}" for w in why], "",
             "| | baseline | after |", "|---|---|---|",
             f"| 커밋 | `{(m(base).get('git') or '')[:7]}` | `{(m(after).get('git') or '')[:7]}` |",
             f"| 실전 1 · 2 | `{(m(base).get('vendor_phase1') or '')[:7]}` · `{(m(base).get('vendor_phase2') or '')[:7]}` "
             f"| `{(m(after).get('vendor_phase1') or '')[:7]}` · `{(m(after).get('vendor_phase2') or '')[:7]}` |",
             f"| 문항 통과율 ({m(base).get('layer')}: {PASS_RULE.get(m(base).get('layer'), '턴 통과')}) "
             f"| {c['base_pass']:.3f} | {c['after_pass']:.3f} |",
             f"| 지연 p95 (ms) | {num(c['p95_ms']['base'])} | {num(c['p95_ms']['after'])} ({pct(c['p95_ms']['change'])}) |",
             f"| 요청당 비용 (KRW) | {num(c['cost']['base'], '{:.3f}')} | {num(c['cost']['after'], '{:.3f}')} ({pct(c['cost']['change'])}) |", "",
             f"- 문항 {c['n']}개, 짝 bootstrap 95% ({RESAMPLES:,}회, seed {SEED}). 바뀐 문항 "
             f"{len(c['wins']) + len(c['losses'])}개 — 바뀐 문항이 적을수록(약 6개 미만) 구간이 실제보다 좁을 수 있다.",
             f"- 문항별 지연 차이(after − baseline) 중앙값 {num(c['latency_diff_median_ms'])} ms, 평균의 95% "
             f"[{c['latency_diff_ci_ms'][0]:.0f}, {c['latency_diff_ci_ms'][1]:.0f}]"]
    if aa is not None:
        lines += ["", f"## A/A 세션 점검: {'통과' if aa['ok'] else '**비교에 못 쓰는 세션**'}",
                  f"- 같은 코드 2회: 뒤집힌 문항 {aa['flips']}개, 통과율 차이 {aa['diff']:+.1%} "
                  f"[{aa['ci'][0]:+.1%}, {aa['ci'][1]:+.1%}], 지연 p95 변화 {pct(aa['p95_change'])}"]
    lines += ["", "## 원천별", "| 원천 | 문항 | 이김 | 짐 |", "|---|---|---|---|",
              *[f"| {s} | {v['n']} | {v['wins']} | {v['losses']} |" for s, v in sorted(c["by_source"].items())]]
    for title, items in (("이긴 문항 (baseline 실패 → after 통과)", c["wins"]), ("진 문항 (baseline 통과 → after 실패)", c["losses"])):
        lines += ["", f"## {title}", "| 문항 | 원천 | 질문 | after 오류 |", "|---|---|---|---|",
                  *[f"| {i['sid']}#{i['turn']} | {i['source']} | {(i['question'] or '')[:60]} | {', '.join(i['errors'])} |"
                    for i in items]]
    if c["new_violations"]:
        lines += ["", "## 새로 생긴 위반", *[f"- {v['sid']}#{v['turn']}: {', '.join(v['what'])}" for v in c["new_violations"]]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.compare", description=__doc__.splitlines()[0])
    parser.add_argument("--base", type=Path, required=True, help="baseline run folder (main)")
    parser.add_argument("--after", type=Path, required=True, help="after run folder (the experiment branch)")
    parser.add_argument("--aa", type=Path, help="baseline run again in the same session (A/A check, optional)")
    parser.add_argument("--scenarios", type=Path, default=ROOT / "evals" / "datasets" / "e2e_golden.jsonl",
                        help="golden scenarios, for each question's source (원천)")
    parser.add_argument("--out", type=Path, help="default: evals/reports/compare-<base>-vs-<after>")
    args = parser.parse_args(argv)
    base, after = load_run(args.base), load_run(args.after)
    try:
        check_comparable(base, after)
        aa = None
        if args.aa:
            again = load_run(args.aa)
            check_comparable(base, again)
            aa = aa_check(base, again)
    except Incomparable as exc:
        print(f"비교 거부: {exc}", file=sys.stderr)
        return 2
    c = paired(base, after, sources_of(args.scenarios))
    decision, why = verdict(c)
    out = args.out or ROOT / "evals" / "reports" / f"compare-{base['manifest']['run_id']}-vs-{after['manifest']['run_id']}"
    out.mkdir(parents=True, exist_ok=True)
    text = render(base, after, c, decision, why, aa)
    (out / "compare.md").write_text(text, encoding="utf-8")
    (out / "compare.json").write_text(json.dumps({"decision": decision, "reasons": why, "aa": aa, **c},
                                                 ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(text)
    print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
