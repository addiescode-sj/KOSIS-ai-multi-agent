"""Slot ablation (§4.4): filter recall per slot combination, with --retriever chroma also Hit/MRR and verdicts."""

from __future__ import annotations

from evals.evaluators import retrieval
from evals.schemas import QueryItem, load_items, load_jsonl
from evals.tasks.common import load_sigungu


def run(args) -> tuple[list[dict], dict, str]:
    """Slot ablation (§4.4): filter recall per combination; with --retriever chroma also Hit/MRR, flips, verdicts."""
    from kosis_rag.parse.node import parse_query

    from evals.runners import ablation
    from evals.runners import retrieval as runner

    items = [i for i in load_items(args.golden) if args.split == "all" or i.split == args.split]
    records = {r.info.key: r for r in runner.load_records()}
    if args.slot_source == "gold":
        slots = {q.qid: ablation.gold_parsed(q) for q in load_jsonl(args.query_golden, QueryItem)}
    else:
        sigungu = load_sigungu()
        slots = {i.qid: parse_query(i.question, sigungu=sigungu) for i in items}
    on = ablation.combos(args.slots.split(","))
    retriever = None
    if args.retriever == "chroma":
        retriever = runner.connect_chroma(args.k)
    rows = ablation.run(items, slots, records, on, retriever, args.k)
    tau = args.tau
    if tau is None and retriever is not None:  # chosen once on `none`, kept for every combination
        tau = retrieval.best_tau([(r["top_score"], not r["answerable"]) for r in rows if r["combo"] == "none" and r["split"] == "dev"])
    s = ablation.summarize(rows, on, slots, tau if tau is not None else 0.0)
    fmt = lambda v, p=3: "—" if v is None else (f"{v:.{p}f}" if isinstance(v, float) else str(v))
    lines = ["| 조합 | n | MRR | Hit@1 | Hit@5 | Hit@10 | 필터 recall | 후보 중앙값 | 0건 답 있음 | 0건 답 없음 | no_period 거절 |",
             "|" + " --- |" * 11]
    for c, m in s["combos"].items():
        lines.append(f"| {c} | {m['n']} | {fmt(m.get('mrr'))} | {fmt(m.get('hit@1'))} | {fmt(m.get('hit@5'))} | {fmt(m.get('hit@10'))} "
                     f"| {fmt(m['filter_recall'])} | {fmt(m['candidates_median'], 0)} | {fmt(m['zero_answerable'])} "
                     f"| {fmt(m['zero_no_answer'])} ({m['n_no_answer']}) | {fmt(m['no_period_refusal'])} ({m['n_no_period']}) |")
    slot_lines = ["| 슬롯 | 슬롯 있는 문항 | only vs none: Hit@5 순증 · Hit@1 · recall Δ | all vs all-s: Hit@5 순증 · Hit@1 · recall Δ | 조건부 ΔMRR | 판정 |",
                  "|" + " --- |" * 6]
    for name, v in s["slots"].items():
        o, mi = v["only"], v["minus"]
        slot_lines.append(f"| {name} | {v['n_with_slot']} | {o['hit5_net']:+d} · {o['hit1_net']:+d} · {o['recall_delta']:+.3f} "
                          f"| {mi['hit5_net']:+d} · {mi['hit1_net']:+d} · {mi['recall_delta']:+.3f} | {fmt(v['conditional_mrr_delta'])} | {v['verdict']} |")
    flips = [f"- {n}: only 좋아짐 {v['only'].get('better', [])} · 나빠짐 {v['only'].get('worse', [])}" for n, v in s["slots"].items()]
    lost = sorted({(r["combo"], r["qid"]) for r in rows if r["answerable"] and not r["filter_pass"]})
    report = "\n".join([
        f"# Slot ablation — {args.stage}", "",
        f"- 슬롯 출처 `{args.slot_source}` · 슬롯 {args.slots} · split `{args.split}` · 검색기 `{args.retriever if retriever else '없음 (필터만)'}`"
        f" · k={min(args.k, 10)} · tau {fmt(tau, 4)} (none에서 한 번 선택)", "",
        *lines, "", *slot_lines, "",
        "- 판정 (§4.4): 도움 = 두 비교 모두 Hit@5 순증 ≥ 2, Hit@1 순감 없음, recall 하락 없음 / 해로움 = 어느 쪽이든 Hit@5 순감 또는 recall 하락 / 그 밖 불분명."
        + (" 검색 없이 돌리면 recall만 보므로 해로움 또는 불분명만 나온다." if retriever is None else ""),
        "- 단계적 완화: 없음 (T2-29 미구현). 0건 = hard 필터가 남긴 표가 0개인 문항 비율, 완화가 시작될 자리다(완화 후 결과는 아님).",
        "", "## 뒤집힌 문항 (Hit@5)", "", *flips,
        "", f"## 필터가 정답 표를 지운 문항 ({len(lost)})", "",
        *[f"- {c} {q}: " + "; ".join(f"{k} {why}" for k, why in next(r for r in rows if r['combo'] == c and r['qid'] == q)['gold_removed'].items())
          for c, q in lost],
    ]) + "\n"
    return rows, s, report


def target(args, op: dict) -> str:
    return args.retriever
