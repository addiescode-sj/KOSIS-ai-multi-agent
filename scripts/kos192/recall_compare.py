"""KOS-192 P3 (+ C1): does searching again with the rewrite bring the gold table into the fused pool? No LLM.

    set PYTHONPATH=.;src;vendor\\phase2\\src;vendor\\phase1;vendor\\phase1\\src
    python scripts\\kos192\\recall_compare.py --collection <collection> --embed-ckpt <embedder .pt> --rerank-ckpt <re-ranker .pt> [--build v1] [--taus 0.1,0.2,0.3]
    python scripts\\kos192\\recall_compare.py ... --preset staged-condyears --build v2-condyears --r1-build v1

Reads data/lab/rewrite/<build>/rewrites.jsonl (gen_rewrites.py) and the dev split; with --r1-build, the R1 rows come
from data/lab/rewrite/<r1-build>/rewrites.jsonl instead (R1 shows the rewriter no candidate, so a build made with
another preset holds the same R1; R2 always comes from --build). For each dev question, pass 1 is the original
question through the search graph up to stage 1 (the L1 preset --preset, default "staged" as before): its fused
pool (retrieve.node.fuse over the pass's lists) is the "never" pool, and rerank1 gives stage1_top_score. For each
variant (R1, R2) and rewrite weight (0.5, 1, 2), an answerable question whose rewrite is "used" is searched again
with the rewrite in the dense and BM25 lists (retrieve.node.retrieve_list) under the original question's table filter
(not recomputed from the rewrite), and the original lists (their own weights) and the two rewrite lists (the rewrite
weight) are fused by weighted RRF (retrieve.fuse.rrf_fuse, pool = options.pool, 50). A question whose rewrite is not
"used" keeps the pass 1 pool. The gold rank is the evaluator's first_rank (best over the OR-groups).

Policies on answerable items, per setting: never (pass 1), always (the rewrite pool when used), gated (the rewrite
only when stage1_top_score < tau; tau triggers 20 / 35 / 50 % of this dev split, or --taus), oracle (per item the
better of never and always). Reports Recall@1 / @5 / @10 / @20 / @50 and the mean reciprocal fuse rank, the items where oracle
beats never by any rank gain and where always is worse than never, and the items whose gold enters (or falls out of)
the top 50 / 20 / 10 under always and oracle compared with never. Stop rule (plan §6.2 P3): oracle brings fewer than 3
answerable items into the top 20; with a preset other than "staged" (plan 07 Q2) it is printed as a diagnosis
only. The rewrites file gives the last row per qid x variant. Writes
data/lab/rewrite/<build>/recall.jsonl (qid, answerable, stage1_top_score, never rank, per setting status and rank: no
question, no key) and recall_summary.json (preset, builds, weights, taus, the aggregates), and prints aggregates only.
An R2 row made with another preset than --preset (its candidates came from another search) is counted and warned.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "vendor" / "phase1" / "src", ROOT / "vendor" / "phase1", ROOT / "vendor" / "phase2" / "src",
           ROOT / "src", ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
os.environ.setdefault("KOSIS_WORKSPACE", str(ROOT))

from kos192_lib import (DEFAULT_PRESET, GOLDEN, LAB, TRIGGER_RATES, VARIANTS, add_preset_arg,  # noqa: E402
                        add_search_args, apply_search_settings, fmt, fuse_rewrite, gold_rank, load_golden, read_jsonl, require_file,
                        row_preset, search_rewrite, stage1_pass, stage1_resources, stage1_signals, trigger_threshold,
                        triggered, utf8_stdout, write_json)

WEIGHTS = (0.5, 1.0, 2.0)  # plan §4.5: REWRITE_WEIGHT candidates
CUTS = (1, 5, 10, 20, 50)  # plan 07 Q2 adds R@1 / R@5: the order inside the pool
MOVE_CUTS = (50, 20, 10)  # cuts the gold table may enter or fall out of
STOP_CUT, STOP_MIN = 20, 3  # plan §6.2 P3: stop when oracle brings fewer than 3 answerable items into the top 20


def setting_name(variant: str, weight: float) -> str:
    """The key of one (variant, rewrite weight) setting, e.g. "R1@w0.5"."""
    return f"{variant}@w{weight:g}"


def better(a: int | None, b: int | None) -> int | None:
    """The better of two gold ranks (lower; None = not in the pool, worst)."""
    if a is None:
        return b
    return a if b is None or a <= b else b


def strictly_better(a: int | None, b: int | None) -> bool:
    """Rank a is better than rank b (in the pool and higher up; None = not in the pool)."""
    return a is not None and (b is None or a < b)


def policy_rank(record: dict, setting: str, policy: str, tau: float | None = None) -> int | None:
    """The gold fuse rank of one answerable record under a policy: never | always | gated (tau) | oracle."""
    never = record["never"]
    always = record["settings"].get(setting, {}).get("rank", never)
    if policy == "never":
        return never
    if policy == "always":
        return always
    if policy == "gated":
        return always if triggered(record["stage1_top_score"], tau) else never
    if policy == "oracle":
        return better(never, always)
    raise ValueError(f"unknown policy {policy!r}")


def inside(rank: int | None, k: int) -> bool:
    """The gold rank is within the top k (None: not in the pool)."""
    return rank is not None and rank <= k


def moves(never: list[int | None], other: list[int | None], cuts: tuple[int, ...] = MOVE_CUTS) -> dict[str, int]:
    """Per cut k: "enter@k" = items whose gold is in the top k under `other` but not under never, "leave@k" = the
    reverse (it falls out). Pure."""
    out: dict[str, int] = {}
    for k in cuts:
        out[f"enter@{k}"] = sum(1 for a, b in zip(never, other) if not inside(a, k) and inside(b, k))
        out[f"leave@{k}"] = sum(1 for a, b in zip(never, other) if inside(a, k) and not inside(b, k))
    return out


def recall_metrics(ranks: list[int | None], cuts: tuple[int, ...] = CUTS) -> dict:
    """n, Recall@k (share of ranks <= k) for each cut, and the mean reciprocal rank (0 for None). Pure."""
    n = len(ranks)
    out: dict = {"n": n}
    for k in cuts:
        out[f"recall@{k}"] = sum(1 for r in ranks if r is not None and r <= k) / n if n else None
    out["mrr"] = sum(1 / r for r in ranks if r) / n if n else None
    return out


def taus_for(records: list[dict], rates: tuple[float, ...] = TRIGGER_RATES) -> dict[str, float | None]:
    """label ("20%" ...) -> tau that triggers that share of the records' stage1_top_score (every dev item, no-answer
    included: the trigger runs before anything knows the answer). Pure."""
    scores = [r["stage1_top_score"] for r in records if r["stage1_top_score"] is not None]
    return {f"{rate:.0%}": trigger_threshold(scores, rate) for rate in rates}


def compare(records: list[dict], settings: list[str], taus: dict[str, float | None]) -> dict[str, dict]:
    """setting -> {"policies": name -> recall_metrics (+ "tau" and "trigger_rate" for a gated one), "oracle_beats_never"
    (any rank gain), "always_worse", "moves": {"always" | "oracle": moves() against never}, "stop" (oracle brings
    fewer than STOP_MIN items into the top STOP_CUT)} over the answerable records. Pure."""
    ans = [r for r in records if r["answerable"]]
    out: dict[str, dict] = {}
    for setting in settings:
        policies: dict[str, dict] = {}
        for policy in ("never", "always"):
            policies[policy] = recall_metrics([policy_rank(r, setting, policy) for r in ans])
        for label, tau in taus.items():
            m = recall_metrics([policy_rank(r, setting, "gated", tau) for r in ans])
            hit = sum(1 for r in ans if triggered(r["stage1_top_score"], tau))
            policies[f"gated@{label}"] = {**m, "tau": tau, "trigger_rate": hit / len(ans) if ans else None}
        policies["oracle"] = recall_metrics([policy_rank(r, setting, "oracle") for r in ans])
        never = [policy_rank(r, setting, "never") for r in ans]
        always = [policy_rank(r, setting, "always") for r in ans]
        oracle = [policy_rank(r, setting, "oracle") for r in ans]
        moved = {"always": moves(never, always), "oracle": moves(never, oracle)}
        out[setting] = {"policies": policies,
                        "oracle_beats_never": sum(1 for a, b in zip(always, never) if strictly_better(a, b)),
                        "always_worse": sum(1 for a, b in zip(always, never) if strictly_better(b, a)),
                        "moves": moved, "stop": moved["oracle"][f"enter@{STOP_CUT}"] < STOP_MIN}
    return out


def load_rewrites(build_dir: Path, r1_dir: Path | None = None) -> dict[tuple[str, str], dict]:
    """(qid, variant) -> the last rewrites.jsonl row (a rerun with another prompt or model appends; the newest row
    wins). With r1_dir, the R1 rows come from that folder's rewrites.jsonl and build_dir's R1 rows are ignored.
    Missing files read as no rows. Side effects: reads the files."""
    rows = read_jsonl(build_dir / "rewrites.jsonl")
    if r1_dir is not None:
        rows = [r for r in rows if r["variant"] != "R1"] + [r for r in read_jsonl(r1_dir / "rewrites.jsonl")
                                                            if r["variant"] == "R1"]
    return {(r["qid"], r["variant"]): r for r in rows}


def preset_mismatch(rewrites: dict[tuple[str, str], dict], preset: str, variant: str = "R2") -> int:
    """Rows of `variant` made with another preset than `preset` (kos192_lib.row_preset). Pure."""
    return sum(1 for (_, v), r in rewrites.items() if v == variant and row_preset(r) != preset)


def render(result: dict[str, dict], n_items: int, n_answerable: int, statuses: dict[str, dict[str, int]],
           preset: str = DEFAULT_PRESET) -> str:
    """The markdown report (aggregates only). The P3 stop rule belongs to the "staged" baseline (plan 02 §6.2); with
    another preset (plan 07 Q2: an order diagnosis, not a stop point) its lines read as a diagnosis only."""
    diagnosis = preset != DEFAULT_PRESET
    lines = [f"# KOS-192 P3 후보 회수 (dev {n_items}, 답 있음 {n_answerable}, Gemma 없음)", "",
             "- 순위 = fuse 풀(50)에서 정답 표(OR 그룹 최고) 순위. never = 원 질문만, always = 재작성 used면 재작성 목록 합침, "
             "gated = 1차 1위 점수 < τ일 때만, oracle = 문항마다 나은 쪽.",
             "- 재작성 상태(답 있음): " + " · ".join(
                 f"{v}: " + ", ".join(f"{s} {c}" for s, c in sorted(statuses.get(v, {}).items())) for v in VARIANTS)]
    for setting, s in result.items():
        lines += ["", f"## {setting}", "", "| 정책 | τ | 트리거(답 있음) | R@1 | R@5 | R@10 | R@20 | R@50 | MRR(fuse) |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for name, m in s["policies"].items():
            tau = fmt(m["tau"], 4) if "tau" in m else "-"
            rate = fmt(m["trigger_rate"], 2) if "trigger_rate" in m else "-"
            lines.append(f"| {name} | {tau} | {rate} | {fmt(m['recall@1'])} | {fmt(m['recall@5'])} | "
                         f"{fmt(m['recall@10'])} | {fmt(m['recall@20'])} | {fmt(m['recall@50'])} | {fmt(m['mrr'])} |")
        lines.append(f"- oracle이 never보다 나은 문항(순위가 조금이라도 오름) {s['oracle_beats_never']} · always가 never보다 "
                     f"나쁜 문항 {s['always_worse']} (답 있음 {n_answerable} 중)")
        for policy, m in s["moves"].items():
            lines.append(f"- {policy} vs never: " + " · ".join(
                f"top {k} 진입 {m[f'enter@{k}']} / 이탈 {m[f'leave@{k}']}" for k in MOVE_CUTS))
        if s["stop"]:
            entered = s['moves']['oracle'][f'enter@{STOP_CUT}']
            lines.append(f"- 진단: oracle로 top {STOP_CUT}에 새로 든 답 있는 문항 {entered}개 < {STOP_MIN} (P3 기준, 참고용)"
                         if diagnosis else f"- **중단 규칙 발동**: oracle로 top {STOP_CUT}에 새로 든 답 있는 문항 "
                         f"{entered}개 < {STOP_MIN} (계획 §6.2 P3)")
    if result:
        best = max(s["moves"]["oracle"][f"enter@{STOP_CUT}"] for s in result.values())
        if diagnosis:
            lines += ["", f"## 진단 (oracle, top {STOP_CUT} 진입; preset {preset})", "",
                      f"- 진단: 최대 {best}개 (P3 중단 기준 {STOP_MIN}개는 staged 기준선용, 여기서는 참고용; "
                      "순서 지표는 R@1·R@5·MRR)"]
        else:
            lines += ["", f"## 중단 규칙 (oracle, top {STOP_CUT} 진입 < {STOP_MIN})", "",
                      (f"- **모든 설정에서 발동** (최대 {best}개): 재작성 진행을 멈추고 보고" if best < STOP_MIN
                       else f"- 발동 안 함: 최대 {best}개 (설정별 표 참고)")]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    parser = argparse.ArgumentParser(description="KOS-192 P3: fuse recall with and without the rewrite lists.")
    parser.add_argument("--golden", type=Path, default=GOLDEN, help="retrieval golden file (default: the KOS-192 set)")
    parser.add_argument("--build", default="v1", help="folder under data/lab/rewrite with rewrites.jsonl (default v1)")
    parser.add_argument("--r1-build", help="take the R1 rows from this build's rewrites.jsonl (default: --build)")
    add_preset_arg(parser)
    parser.add_argument("--weights", default=",".join(f"{w:g}" for w in WEIGHTS), help="rewrite RRF weights")
    parser.add_argument("--taus", help="fixed tau values for gated, comma separated (default: 20/35/50%% trigger rates)")
    parser.add_argument("--limit", type=int, help="only the first N dev questions (smoke run)")
    add_search_args(parser)
    args = parser.parse_args(argv)

    from kosis_rag.graph.search import build_graph
    from kosis_rag.retrieve import node as retrieve_step
    from kosis_rag.retrieve.schemas import FuseInput

    out_dir = LAB / args.build
    r1_dir = LAB / args.r1_build if args.r1_build else None
    require_file(out_dir / "rewrites.jsonl", f"rewrites file (run gen_rewrites.py --build {args.build} first)")
    if r1_dir is not None:
        require_file(r1_dir / "rewrites.jsonl", f"R1 rewrites file (--r1-build {args.r1_build})")
    rewrites = load_rewrites(out_dir, r1_dir)
    if not rewrites:
        raise SystemExit(f"no rewrites in {out_dir / 'rewrites.jsonl'}: run gen_rewrites.py --build {args.build} first")
    mismatch = preset_mismatch(rewrites, args.preset)
    if mismatch:
        print(f"warning: {mismatch} R2 rows were made with another preset than {args.preset} (their candidate names "
              "came from another search)", file=sys.stderr)
    weights = [float(w) for w in args.weights.split(",") if w.strip()]
    settings = [setting_name(v, w) for v in VARIANTS for w in weights]
    apply_search_settings(args)
    items = [i for i in load_golden(args.golden).values() if i.split == "dev"]
    items = items[: args.limit] if args.limit else items
    resources = stage1_resources(args.preset)
    graph = build_graph(resources)
    keys = lambda docs: [d.metadata["key"] for d in docs]  # noqa: E731

    records, failed = [], 0
    statuses: dict[str, dict[str, int]] = {v: {} for v in VARIANTS}
    for n, item in enumerate(items, 1):
        try:
            state = stage1_pass(graph, item.question)
        except Exception as exc:  # left out of every policy, counted
            failed += 1
            print(f"[{n}/{len(items)}] {item.qid} search failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        never_docs = retrieve_step.fuse(FuseInput(runs=state.runs, run_removed=state.run_removed), resources).docs
        record = {"qid": item.qid, "answerable": item.answerable, "stage1_top_score": stage1_signals(state)[0],
                  "never": gold_rank(item, keys(never_docs)) if item.answerable else None, "settings": {}}
        if item.answerable:
            for variant in VARIANTS:
                row = rewrites.get((item.qid, variant))
                status = row["status"] if row else "missing"
                statuses[variant][status] = statuses[variant].get(status, 0) + 1
                # the rewrite lists under the original filter, fused with pass 1's lists (kos192_lib.fuse_rewrite)
                again = search_rewrite(row["rewrite"], state.table_filter, resources) if status == "used" else []
                for w in weights:
                    rank = record["never"]
                    if again:
                        rank = gold_rank(item, keys(fuse_rewrite(state.runs, again, w, resources)))
                    record["settings"][setting_name(variant, w)] = {"status": status, "rank": rank}
        records.append(record)
        print(f"[{n}/{len(items)}] {item.qid}")

    taus = ({f"tau{i + 1}": float(t) for i, t in enumerate(args.taus.split(",")) if t.strip()} if args.taus
            else taus_for(records))
    result = compare(records, settings, taus)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "recall.jsonl").open("w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    write_json(out_dir / "recall_summary.json", {
        "preset": args.preset, "build": args.build, "r1_build": args.r1_build or args.build,
        "r2_preset_mismatch": mismatch, "weights": weights, "taus": taus, "n_items": len(records),
        "search_failed": failed, "statuses": statuses, "settings": result})
    print(f"preset {args.preset} · R2 from {args.build} · R1 from {args.r1_build or args.build}")
    print(render(result, len(records), sum(1 for r in records if r["answerable"]), statuses, args.preset), end="")
    if failed:
        print(f"- 검색 실패로 빠진 문항 {failed}")
    print(f"-> {out_dir / 'recall.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
