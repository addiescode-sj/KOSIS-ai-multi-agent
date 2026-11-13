"""report.html for one run folder: score tiles and charts, slot-level table, ops metrics and their trend over runs.

    python -m evals.report_html --run evals/reports/<run_id>        # re-render; run_eval calls write() itself

Every layer gets one (T3-20): l1 / l3 / e2e / agent have their own views; l0, l2, ablation and l012 show report.md as
HTML with their ops. The agent view shows each conversation turn by turn with its span waterfall and Jaeger link.
Self-contained (inline SVG and CSS, no CDN), so it opens offline. Light and dark follow the OS. Every mark has a
native hover tooltip (<title>). Colors follow the dataviz reference palette: categorical slots 1-3 for Hit@1/5/10,
status colors (with icon and label) for PASS/PARTIAL/FAIL, one blue for single-series bars.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from urllib.parse import quote

from evals.evaluators.calculation import ERROR_INFO
from evals.ops import read_history
from evals.trace import SEARCH_TOOL, STAGES, STAT_TOOL

QTYPE_ORDER = ["all", "exact_name", "concept", "sibling", "multi_condition", "period_cycle", "multi_table", "ambiguous"]
VERDICTS = [("PASS", "good", "✓"), ("PARTIAL", "warning", "△"), ("FAIL", "critical", "✕")]

CSS = """
.viz-root{color-scheme:light;--surface-0:#f5f5f3;--surface-1:#fcfcfb;--border:#e2e1dc;--text-primary:#0b0b0b;
--text-secondary:#52514e;--text-muted:#7a7974;--grid:#ecebe7;--series-1:#2a78d6;--series-2:#eb6834;--series-3:#1baf7a;
--muted-bar:#b9b8b2;--good:#0ca30c;--warning:#fab219;--critical:#d03b3b}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;--surface-0:#121211;
--surface-1:#1a1a19;--border:#33332f;--text-primary:#f0efec;--text-secondary:#c3c2b7;--text-muted:#8f8e86;--grid:#2a2a27;
--series-1:#3987e5;--series-2:#d95926;--series-3:#199e70;--muted-bar:#5a5953}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface-0:#121211;--surface-1:#1a1a19;--border:#33332f;
--text-primary:#f0efec;--text-secondary:#c3c2b7;--text-muted:#8f8e86;--grid:#2a2a27;--series-1:#3987e5;--series-2:#d95926;
--series-3:#199e70;--muted-bar:#5a5953}
*{box-sizing:border-box}body{margin:0}
.viz-root{background:var(--surface-0);color:var(--text-primary);font:14px/1.5 system-ui,-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;min-height:100vh}
main{max-width:1100px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:0 0 4px}.meta,.note{color:var(--text-secondary);font-size:12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin:16px 0 24px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.tile .v{font-size:24px;font-weight:600;font-variant-numeric:tabular-nums}.tile .l{color:var(--text-secondary);font-size:12px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:16px;margin:0 0 16px;overflow-x:auto}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--text-secondary);margin:6px 0}
.sw{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
svg text{fill:var(--text-secondary);font-size:11px}svg .val{fill:var(--text-primary)}
svg rect.mark:hover,svg circle.mark:hover{opacity:.8}
table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid var(--border);padding:5px 6px;text-align:left;vertical-align:top}
th{color:var(--text-secondary);font-weight:600}td.num{font-variant-numeric:tabular-nums;text-align:right}
.chip{display:inline-block;border-radius:6px;padding:0 6px;margin:1px 3px 1px 0;border:1px solid var(--border);white-space:nowrap}
.ok{color:var(--good)}.warn{color:var(--text-primary)}.bad{color:var(--critical)}
details summary{cursor:pointer;color:var(--text-secondary)}
.chip.err{border-color:var(--critical);color:var(--critical)}.chip.blame{border-width:2px;font-weight:600}
.tools{color:var(--text-secondary);font-size:12px}
.info{margin:12px 0 0;padding-left:18px;font-size:13px}.info li{margin:0 0 8px}a{color:var(--series-1)}
.guide{border-left:4px solid var(--series-1)}.guide ul{margin:6px 0 0;padding-left:18px;font-size:13px}.guide li{margin:0 0 4px}
.md h3{font-size:14px;margin:14px 0 4px}.md ul{padding-left:18px;font-size:13px}.md table{margin:6px 0 10px}
.conv{border:1px solid var(--border);border-radius:10px;margin:0 0 10px;background:var(--surface-1)}
.conv>summary{padding:8px 12px;color:var(--text-primary)}.conv .turns{padding:0 12px 10px}
.turn{border-top:1px solid var(--border);padding:8px 0}.turn .q{font-weight:600}.turn .a{color:var(--text-secondary);margin:4px 0;white-space:pre-wrap}
.wf{margin:6px 0 0;font-size:11px}.wf .r{display:grid;grid-template-columns:200px 1fr 64px;gap:6px;align-items:center;margin:1px 0}
.wf .n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-secondary)}
.wf .track{position:relative;height:10px;background:var(--grid);border-radius:3px}
.wf .bar{position:absolute;top:0;height:10px;border-radius:3px;min-width:2px}
.wf .turn-k{background:var(--muted-bar)}.wf .node-k{background:var(--series-1)}.wf .tool-k{background:var(--series-3)}.wf .llm-k{background:var(--series-2)}
.wf .err{outline:2px solid var(--critical)}
"""


def esc(x) -> str:
    return html.escape(str(x))


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def tile(value: str, label: str) -> str:
    return f'<div class="tile"><div class="v">{esc(value)}</div><div class="l">{esc(label)}</div></div>'


def legend(items: list[tuple[str, str]]) -> str:
    return '<div class="legend">' + "".join(f'<span><span class="sw" style="background:var({c})"></span>{esc(n)}</span>' for n, c in items) + "</div>"


def hbar_groups(cats: list[str], series: list[tuple[str, str, list[float]]], counts: list[int]) -> str:
    """Grouped horizontal bars on a 0-1 scale; one row group per category, one bar per series."""
    bar, gap, group_gap, left, width = 9, 2, 12, 150, 420
    n = len(series)
    gh = n * bar + (n - 1) * gap
    h = len(cats) * (gh + group_gap) + 24
    out = [f'<svg viewBox="0 0 {left + width + 60} {h}" width="100%" role="img">']
    for t in (0, 0.25, 0.5, 0.75, 1):
        x = left + t * width
        out.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{h - 18}" stroke="var(--grid)"/><text x="{x}" y="{h - 4}" text-anchor="middle">{int(t * 100)}%</text>')
    for i, cat in enumerate(cats):
        y0 = i * (gh + group_gap)
        out.append(f'<text x="{left - 8}" y="{y0 + gh / 2 + 4}" text-anchor="end">{esc(cat)} (n={counts[i]})</text>')
        for j, (name, color, vals) in enumerate(series):
            v = vals[i]
            y = y0 + j * (bar + gap)
            w = max(v * width, 0.5)
            out.append(f'<rect class="mark" x="{left}" y="{y}" width="{w:.1f}" height="{bar}" rx="2" fill="var({color})">'
                       f'<title>{esc(cat)} · {esc(name)}: {pct(v)}</title></rect>')
        best = series[-1][2][i]
        out.append(f'<text class="val" x="{left + best * width + 6:.1f}" y="{y0 + gh - 1}">{pct(best)}</text>')
    return "".join(out) + "</svg>"


def vbars(labels: list[str], values: list[float], colors: list[str], fmt=lambda v: f"{v:g}", height: int = 160) -> str:
    left, top, bw, gap = 36, 14, 26, 6
    vmax = max(values + [1e-9])
    w = left + len(labels) * (bw + gap) + 10
    out = [f'<svg viewBox="0 0 {w} {height + 34}" width="100%" style="max-width:{max(w * 1.3, 260):.0f}px" role="img">']
    for t in (0, 0.5, 1):
        y = top + height - t * height
        out.append(f'<line x1="{left}" y1="{y}" x2="{w}" y2="{y}" stroke="var(--grid)"/><text x="{left - 4}" y="{y + 4}" text-anchor="end">{fmt(vmax * t)}</text>')
    for i, (lab, v, c) in enumerate(zip(labels, values, colors)):
        x = left + i * (bw + gap) + gap
        bh = v / vmax * height if vmax else 0
        out.append(f'<rect class="mark" x="{x}" y="{top + height - bh:.1f}" width="{bw}" height="{max(bh, 0.5):.1f}" rx="2" fill="var({c})"><title>{esc(lab)}: {fmt(v)}</title></rect>')
        out.append(f'<text x="{x + bw / 2}" y="{top + height + 14}" text-anchor="middle">{esc(lab)}</text>')
    return "".join(out) + "</svg>"


def hbars(labels: list[str], values: list[float]) -> str:
    left, width, bar, gap = 150, 260, 14, 8
    vmax = max(values + [1e-9])
    h = len(labels) * (bar + gap)
    out = [f'<svg viewBox="0 0 {left + width + 40} {h}" width="100%" role="img">']
    for i, (lab, v) in enumerate(zip(labels, values)):
        y = i * (bar + gap)
        w = v / vmax * width
        out.append(f'<text x="{left - 8}" y="{y + 11}" text-anchor="end">{esc(lab)}</text>'
                   f'<rect class="mark" x="{left}" y="{y}" width="{max(w, 0.5):.1f}" height="{bar}" rx="2" fill="var(--series-1)"><title>{esc(lab)}: {v:g}</title></rect>'
                   f'<text class="val" x="{left + w + 6:.1f}" y="{y + 11}">{v:g}</text>')
    return "".join(out) + "</svg>"


def stacked_verdicts(rows: list[tuple[str, dict]]) -> str:
    left, width, bar, gap = 130, 260, 16, 10
    h = len(rows) * (bar + gap) + 4
    out = [f'<svg viewBox="0 0 {left + width + 70} {h}" width="100%" role="img">']
    for i, (name, counts) in enumerate(rows):
        total = sum(counts.values()) or 1
        y, x = i * (bar + gap), left
        out.append(f'<text x="{left - 8}" y="{y + 12}" text-anchor="end">{esc(name)} (n={sum(counts.values())})</text>')
        for v, color, icon in VERDICTS:
            c = counts.get(v, 0)
            if c:
                w = c / total * width
                out.append(f'<rect class="mark" x="{x:.1f}" y="{y}" width="{max(w - 2, 0.5):.1f}" height="{bar}" rx="2" fill="var(--{color})"><title>{name} · {icon} {v}: {c}</title></rect>')
                x += w
        out.append(f'<text class="val" x="{left + width + 6}" y="{y + 12}">{counts.get("PASS", 0)}/{sum(counts.values())}</text>')
    return "".join(out) + "</svg>"


def trend(points: list[tuple[str, float]], fmt) -> str:
    """One measure across runs (one axis); dots + 2px line, run label on hover."""
    if len(points) < 2:
        return '<p class="note">같은 계층의 실행이 2개 이상 쌓이면 추이가 그려진다.</p>'
    if not any(v for _, v in points):
        return f'<p class="note">{len(points)}회 모두 0이다.</p>'
    left, top, w, h = 50, 12, 300, 110
    vmax = max(v for _, v in points)
    xs = [left + i * w / (len(points) - 1) for i in range(len(points))]
    ys = [top + h - v / vmax * h for _, v in points]
    out = [f'<svg viewBox="0 0 {left + w + 20} {h + 30}" width="100%" role="img">']
    for t in (0, 0.5, 1):
        y = top + h - t * h
        out.append(f'<line x1="{left}" y1="{y}" x2="{left + w}" y2="{y}" stroke="var(--grid)"/><text x="{left - 4}" y="{y + 4}" text-anchor="end">{fmt(vmax * t)}</text>')
    out.append('<polyline fill="none" stroke="var(--series-1)" stroke-width="2" points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys)) + '"/>')
    for (label, v), x, y in zip(points, xs, ys):
        out.append(f'<circle class="mark" cx="{x:.1f}" cy="{y:.1f}" r="5" fill="var(--series-1)" stroke="var(--surface-1)" stroke-width="2"><title>{esc(label)}: {fmt(v)}</title></circle>')
    out.append(f'<text x="{left}" y="{top + h + 22}">{esc(points[0][0][:12])}</text><text x="{left + w}" y="{top + h + 22}" text-anchor="end">{esc(points[-1][0][:12])}</text>')
    return "".join(out) + "</svg>"


def histogram(values: list[float], bins: int = 10) -> tuple[list[str], list[float]]:
    if not values:
        return [], []
    lo, hi = min(values), max(values)
    step = (hi - lo) / bins or 1
    counts = [0] * bins
    for v in values:
        counts[min(int((v - lo) / step), bins - 1)] += 1
    return [f"{lo + i * step:.0f}" for i in range(bins)], counts


def l1_body(m: dict, rows: list[dict]) -> str:
    s = m["summary"]
    agg = s["metrics"]
    a = agg.get("all", {})
    tiles = [tile(pct(a.get("hit@1", 0)), "Hit@1"), tile(pct(a.get("hit@5", 0)), "Hit@5"), tile(f"{a.get('rr', 0):.3f}", "MRR"),
             tile(pct(a.get("slot_recall@5", 0)), "SlotRecall@5"), tile(pct(a.get("complete@2", 0)), "Complete@2"),
             tile(f"{s['no_answer']['f1']:.3f}", f"답 없음 F1 (τ={s['tau']:.3g})")]
    cats = [c for c in QTYPE_ORDER if c in agg]
    chart = hbar_groups(cats, [("Hit@1", "--series-1", [agg[c]["hit@1"] for c in cats]),
                               ("Hit@5", "--series-2", [agg[c]["hit@5"] for c in cats]),
                               ("Hit@10", "--series-3", [agg[c]["hit@10"] for c in cats])], [agg[c]["n"] for c in cats])
    answerable = [r for r in rows if r.get("answerable")]
    labels = [str(i) for i in range(1, 11)] + ["밖"]
    counts = [sum(1 for r in answerable if r.get("rank") == i) for i in range(1, 11)] + [sum(1 for r in answerable if not r.get("rank") or r["rank"] > 10)]
    ranks = vbars(labels, counts, ["--series-1"] * 10 + ["--muted-bar"])
    multi = [r for r in answerable if len(r.get("slot_ranks") or []) > 1]

    def chip(rank):
        if rank is None:
            return '<span class="chip bad">✕ 없음</span>'
        cls, icon = ("ok", "✓") if rank <= 2 else ("warn", "△")
        return f'<span class="chip {cls}">{icon} {rank}위</span>'

    slot_rows = "".join(f'<tr><td>{esc(r["qid"])}</td><td>{esc(r.get("question", ""))}</td><td>'
                        + "".join(f"슬롯{i + 1} {chip(k)}" for i, k in enumerate(r["slot_ranks"]))
                        + f'</td><td class="num">{r["slot_recall@10"]:.2f}</td></tr>' for r in multi)
    na = s["no_answer"]
    return "".join([
        '<div class="tiles">', *tiles, "</div>",
        '<div class="card"><h2>유형별 Hit@k</h2><p class="note">답 있는 문항 기준. 막대 끝 숫자는 Hit@10.</p>',
        legend([("Hit@1", "--series-1"), ("Hit@5", "--series-2"), ("Hit@10", "--series-3")]), chart, "</div>",
        '<div class="grid2"><div class="card"><h2>첫 정답 순위 분포</h2><p class="note">어느 슬롯이든 첫 정답이 나온 순위. "밖" = 상위 10개에 없음.</p>', ranks, "</div>",
        f'<div class="card"><h2>답 없음 판정</h2><p class="note">top-1 점수 &lt; τ이면 답 없음. τ는 dev에서 F1 최대로 고른다.</p>'
        f'<table><tr><th>Precision</th><th>Recall</th><th>F1</th><th>TP</th><th>FP</th><th>FN</th></tr><tr>'
        f'<td class="num">{na["precision"]:.3f}</td><td class="num">{na["recall"]:.3f}</td><td class="num">{na["f1"]:.3f}</td>'
        f'<td class="num">{na["tp"]}</td><td class="num">{na["fp"]}</td><td class="num">{na["fn"]}</td></tr></table></div></div>',
        '<div class="card"><h2>슬롯별 채점 (복합 질문)</h2><p class="note">✓ 2위 안 · △ 3~10위 · ✕ 10위 밖. 오른쪽은 SlotRecall@10.</p>',
        f"<table><tr><th>qid</th><th>질문</th><th>슬롯별 첫 정답 순위</th><th>SlotRecall@10</th></tr>{slot_rows}</table>" if multi else '<p class="note">이 split에 복합 문항이 없다.</p>',
        "</div>",
    ])


def l3_body(m: dict, rows: list[dict]) -> str:
    v = m["summary"]["verdicts"]
    allv = v.get("all", {})
    n = sum(allv.values()) or 1
    tiles = [tile(f"{allv.get('PASS', 0)}/{n}", "PASS"), tile(pct(allv.get("PASS", 0) / n), "PASS 비율"),
             tile(str(allv.get("PARTIAL", 0)), "PARTIAL"), tile(str(allv.get("FAIL", 0)), "FAIL")]
    kinds = [k for k in ["all", "answerable", "not_answerable", "clarify", "default_allowed"] if k in v]
    errors = sorted(m["summary"]["errors"].items(), key=lambda kv: -kv[1])
    err_chart = hbars([k for k, _ in errors], [c for _, c in errors]) if errors else '<p class="note">오류 없음</p>'
    err_chart += error_info(errors, rows)
    return "".join([
        '<div class="tiles">', *tiles, "</div>",
        '<div class="grid2"><div class="card"><h2>kind별 판정</h2>',
        legend([(f"{icon} {name}", f"--{color}") for name, color, icon in VERDICTS]), stacked_verdicts([(k, v[k]) for k in kinds]), "</div>",
        '<div class="card"><h2>오류 유형</h2>', err_chart, "</div></div>",
    ])


def error_info(errors: list[tuple[str, int]], rows: list[dict]) -> str:
    """Under the error chart: what each error means, the stage it was blamed on and which questions have it."""
    items = []
    for name, count in errors:
        hit = [r for r in rows if name in r.get("errors", [])]
        stages = sorted({r["failure"]["label"] for r in hit if r.get("failure")})
        qids = ", ".join(r["qid"] for r in hit[:6]) + (" …" if len(hit) > 6 else "")
        items.append(f'<li><b>{esc(name)}</b> {count}건 — {esc(ERROR_INFO.get(name, "설명 없음 (evaluators/calculation.py ERROR_INFO)"))}'
                     f'<div class="tools">{"실패 단계: " + esc(", ".join(stages)) + " · " if stages else ""}문항: {esc(qids)}</div></li>')
    return f'<ul class="info">{"".join(items)}</ul>' if items else ""


def e2e_body(m: dict, rows: list[dict]) -> str:
    """L3's verdict view plus where it went wrong: retrieval (top 1 was another table) or the answer."""
    s = m["summary"]
    n = len(rows) or 1
    causes = s["causes"]
    tiles = [tile(f"{s['top1_right']}/{len(rows)}", "검색 1위 = 정답 표"), tile(pct(s["top1_right"] / n), "검색 1위 정답 비율")]
    split = [("1위 정답 표", s["by_top1"]["right"]), ("1위 다른 표", s["by_top1"]["wrong"])]
    cause_chart = hbars(["PASS", "검색 실패", "답 실패"], [causes.get("pass", 0), causes.get("retrieval", 0), causes.get("answer", 0)])
    return "".join([
        l3_body(m, rows), '<div class="tiles">', *tiles, "</div>",
        '<div class="grid2"><div class="card"><h2>검색 1위가 맞았을 때와 틀렸을 때</h2>',
        '<p class="note">틀린 표에서 PASS는 두 표의 숫자가 같을 때만 나온다.</p>',
        stacked_verdicts([(k, v) for k, v in split if v]), "</div>",
        '<div class="card"><h2>실패 원인</h2><p class="note">검색 = 1위가 다른 표 · 답 = 표는 맞았는데 답이 틀림.</p>', cause_chart, "</div></div>",
    ])


def ops_body(op: dict, rows: list[dict], history: list[dict]) -> str:
    c = op["cost"]
    no_llm = not (op["tokens"]["input"] or op["tokens"]["output"])
    cost = "—" if no_llm else f"{c['total']:.2f} {c['currency']}" if c["total"] is not None else "단가 미설정"
    tiles = [tile(f"{op['latency_ms']['p50']:.0f} ms", "Latency p50"), tile(f"{op['latency_ms']['p95']:.0f} ms", "Latency p95"),
             tile(f"{op['tokens']['input']:,}", "입력 토큰"), tile(f"{op['tokens']['output']:,}", "출력 토큰"),
             tile(cost, "Cost (LLM 호출 없음)" if no_llm else "Cost"), tile(pct(op["tool_failure_rate"]), f"Tool Failure Rate ({op['tool_calls']}회)"),
             tile(pct(op["retry_rate"]), "재시도 비율")]
    latencies = [r["latency_ms"] for r in rows if "latency_ms" in r]
    labels, counts = histogram(latencies)
    lat = (vbars(labels, counts, ["--series-1"] * len(labels)) if labels and max(latencies) > 0
           else '<p class="note">모든 행의 지연이 0 ms다(모델·검색 호출 없이 함수만 실행).</p>')
    tok = [r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in rows]
    tok_chart = ""
    if any(tok):
        tl, tc = histogram(tok)
        tok_chart = f'<div class="card"><h2>문항당 토큰 분포</h2><p class="note">x축은 구간 시작값(토큰).</p>{vbars(tl, tc, ["--series-1"] * len(tl))}</div>'
    note = "" if no_llm or c["total"] is not None else f'<p class="note">비용: 단가 미설정 모델 {esc(", ".join(c["unpriced_models"]) or "-")} → evals/pricing.json에 1K 토큰 단가를 넣으면 계산된다.</p>'
    trends = "".join([
        f'<div class="card"><h2>{title}</h2>{trend([(h["stage"], h[key] or 0) for h in history], fmt)}</div>'
        for title, key, fmt in [("Latency p95 추이 (ms)", "latency_p95_ms", lambda v: f"{v:.0f}"),
                                ("입력+출력 토큰 추이", "total_tokens", lambda v: f"{v:,.0f}"),
                                ("Tool Failure Rate 추이", "tool_failure_rate", lambda v: f"{v * 100:.0f}%")]])
    return "".join(['<h2 style="margin-top:28px">운영 지표</h2>', '<div class="tiles">', *tiles, "</div>", note,
                    '<div class="grid2"><div class="card"><h2>Latency 분포</h2><p class="note">x축은 구간 시작값(ms), y축은 문항 수.</p>', lat, "</div>",
                    tok_chart, "</div>",
                    f'<h2 style="margin-top:12px">실행 간 추이 (같은 계층, {len(history)}회)</h2><div class="grid2">', trends, "</div>"])


def jaeger_search(m: dict) -> str:
    """Jaeger search for this run's layer: service kosis-agent, tags deployment.environment.name=eval + kosis.eval.layer (T3-32)."""
    j = m.get("jaeger") or {}
    tags = json.dumps({"deployment.environment.name": j.get("environment", "eval"),
                       **({"kosis.eval.layer": j["layer"]} if j.get("layer") else {})})
    return f'{j.get("ui", "")}/search?service={j.get("service", "kosis-agent")}&tags={quote(tags)}'


def trace_link(ui: str | None, trace_id: str | None) -> str:
    return f'<a href="{esc(ui)}/trace/{esc(trace_id)}" target="_blank">trace</a>' if ui and trace_id else "-"


def timeline(r: dict) -> str:
    """The question's span path as chips: ✕ = error span, bold = the span blamed for the failure."""
    blamed = (r.get("failure") or {}).get("span")
    if nodes := r.get("search_spans"):  # LangGraph search (T2-36): one chip per node
        chips = [(s["name"], s["status"], s["duration_ms"]) for s in nodes]
    else:
        chips = [(SEARCH_TOOL, "ok" if r.get("right_table", True) else "error", None)] if "right_table" in r else []
    if "verdict" in r:  # L3 / e2e: the tool and its Phase 1 spans
        chips.append((STAT_TOOL, "error" if blamed == STAT_TOOL else "ok", None))
        chips += [(s["name"], s["status"], s["duration_ms"]) for s in r.get("spans") or []]
    out = []
    for name, status, ms in chips:
        cls = "chip" + (" err" if status == "error" else "") + (" blame" if name == blamed else "")
        out.append(f'<span class="{cls}" title="{esc(name)} · {esc(status)}{"" if ms is None else f" · {ms:.0f} ms"}">'
                   f'{"✕ " if status == "error" else ""}{esc(name)}</span>')
    return "".join(out)


def search_failures_body(m: dict, rows: list[dict]) -> str:
    """L1 over the LangGraph search: misses per node (evals.trace.blame_search) and, per miss, the node path."""
    counts = m["summary"].get("search_failures")
    if counts is None:
        return ""
    ui = (m.get("jaeger") or {}).get("ui")
    failed = [r for r in rows if r.get("failure")]
    nodes = sorted(counts, key=counts.get, reverse=True)
    chart = hbars(nodes, [counts[n] for n in nodes]) if nodes else '<p class="note">실패 없음</p>'
    body = "".join(
        f'<tr><td>{esc(r["qid"])}</td><td>{esc(r.get("question", ""))}</td><td>{esc(r["qtype"])}</td>'
        f'<td><b>{esc(r["failure"]["label"])}</b><div class="tools">{esc(r["failure"]["span"] or "-")}</div></td>'
        f'<td>{esc(r["failure"]["reason"])}</td><td>{timeline(r)}</td><td>{trace_link(ui, r.get("trace_id"))}</td></tr>'
        for r in failed)
    table = (f'<table><tr><th>qid</th><th>질문</th><th>qtype</th><th>실패 · 노드</th><th>사유</th><th>노드 경로</th><th>Jaeger</th></tr>{body}</table>'
             if failed else '<p class="note">실패 문항 없음</p>')
    return "".join([
        f'<div class="card"><h2>실패 노드 (LangGraph, {len(failed)}건)</h2>',
        '<p class="note">Hit@k 놓침은 정답 표를 top k 밖으로 마지막으로 내린 노드(처음부터 밖이면 retrieve), 답 없음 오판은 gate. '
        '굵은 테두리 = 판정한 노드, 마우스를 올리면 지연이 보인다.</p>', chart, table, "</div>"])


def failures_body(m: dict, rows: list[dict]) -> str:
    """Stage counts (evals.trace.blame) and, per non-PASS question, the stage, span, reason, path and Jaeger link."""
    counts = m["summary"].get("failures")
    if counts is None:
        return ""
    stages = [k for k in STAGES if counts.get(k)]
    chart = hbars([STAGES[k] for k in stages], [counts[k] for k in stages]) if stages else '<p class="note">실패 없음</p>'
    ui = (m.get("jaeger") or {}).get("ui")
    failed = [r for r in rows if r.get("failure")]
    body = "".join(
        f'<tr><td>{esc(r["qid"])}</td><td>{esc(r.get("question", ""))}</td><td>{esc(r["verdict"])}</td>'
        f'<td><b>{esc(r["failure"]["label"])}</b><div class="tools">{esc(r["failure"]["span"] or "-")}</div></td>'
        f'<td>{esc(r["failure"]["reason"])}</td><td>{timeline(r)}</td>'
        f'<td>{trace_link(ui, r.get("trace_id"))}</td></tr>'
        for r in failed)
    table = (f'<table><tr><th>qid</th><th>질문</th><th>판정</th><th>실패 단계 · span</th><th>사유</th><th>호출 도구 · span 경로</th><th>Jaeger</th></tr>{body}</table>'
             if failed else "")
    jaeger = (f'Jaeger: <a href="{esc(jaeger_search(m))}" target="_blank">kosis-agent · 평가 trace</a>' if ui
              else "Jaeger 미전송 (OTEL_EXPORTER_OTLP_ENDPOINT를 설정하고 docker compose --profile obs up jaeger)")
    order = " &gt; ".join(STAGES.values())
    return "".join([
        '<div class="card"><h2>실패 단계 (trace)</h2>',
        f'<p class="note">non-PASS 문항마다 첫 번째로 맞는 규칙 하나: {order}. {jaeger}</p>', chart, "</div>",
        f'<div class="card"><h2>실패 문항별 원인 ({len(failed)}건)</h2>'
        '<p class="note">span 경로: 굵은 테두리 = 실패로 판정한 span, ✕ = 상태 error인 span(재시도 전 verify처럼 정상 흐름일 수 있음). 마우스를 올리면 지연이 보인다.</p>',
        table or '<p class="note">실패 문항 없음</p>', "</div>",
    ])


def rows_table(layer: str, rows: list[dict]) -> str:
    if layer == "l1":
        head = ["qid", "split", "qtype", "질문", "첫 정답", "슬롯 순위", "top-3", "ms"]
        body = "".join(f'<tr><td>{esc(r["qid"])}</td><td>{esc(r["split"])}</td><td>{esc(r["qtype"])}</td><td>{esc(r.get("question", ""))}</td>'
                       f'<td class="num">{esc(r.get("rank") or ("답 없음" if not r["answerable"] else "-"))}</td><td>{esc(r.get("slot_ranks", ""))}</td>'
                       f'<td>{esc(", ".join(r["keys"][:3]))}</td><td class="num">{r["latency_ms"]:.1f}</td></tr>' for r in rows)
    elif layer == "e2e":
        head = ["qid", "kind", "판정", "원인", "실패 단계", "쓴 표", "정답 순위", "오류", "ms", "토큰"]
        body = "".join(f'<tr><td>{esc(r["qid"])}</td><td>{esc(r["kind"])}</td><td>{esc(r["verdict"])}</td><td>{esc(r["cause"])}</td>'
                       f'<td>{esc((r.get("failure") or {}).get("label", "-"))}</td>'
                       f'<td>{esc(r["used_table"] or "-")}{"" if r["right_table"] else " ✕"}</td><td class="num">{esc(r["gold_rank"] or "밖")}</td>'
                       f'<td>{esc(", ".join(r["errors"]))}</td><td class="num">{r.get("latency_ms", 0):.0f}</td>'
                       f'<td class="num">{r.get("input_tokens", 0) + r.get("output_tokens", 0):,}</td></tr>' for r in rows)
    else:
        head = ["qid", "kind", "판정", "실패 단계", "오류", "ms", "토큰"]
        body = "".join(f'<tr><td>{esc(r["qid"])}</td><td>{esc(r["kind"])}</td><td>{esc(r["verdict"])}</td>'
                       f'<td>{esc((r.get("failure") or {}).get("label", "-"))}</td><td>{esc(", ".join(r["errors"]))}</td>'
                       f'<td class="num">{r.get("latency_ms", 0):.0f}</td><td class="num">{r.get("input_tokens", 0) + r.get("output_tokens", 0):,}</td></tr>' for r in rows)
    return ('<details class="card"><summary>문항별 결과 표 (' + str(len(rows)) + "건)</summary><table><tr>"
            + "".join(f"<th>{h}</th>" for h in head) + "</tr>" + body + "</table></details>")


LAYER_TITLES = {"l0": "L0 메타데이터", "l1": "L1 검색", "l1-gold": "L1 슬롯 필터 (정답 슬롯)", "l1-pred": "L1 슬롯 필터 (파서 슬롯)",
                "l2": "L2 질의 이해", "ablation": "L1 슬롯 ablation", "l012": "L0 + L1 필터 + L2", "l3": "L3 계산",
                "e2e": "L4 종단 간 (e2e)", "agent": "에이전트 (멀티턴 · 멀티쿼리)"}
NO_TRACE = "이 계층은 모델·그래프를 부르지 않아 trace가 없다(지연은 함수 실행 시간)."
GUIDE = {  # layer -> how to read this page, one line per item
    "l0": ["채움률: 필터가 쓰는 메타데이터 필드가 채워진 표의 비율. 높을수록 좋다(골든 정답 표 기준이 더 중요).",
           "미매핑: 질문 어휘로 고를 수 없는 이름의 비율. 낮을수록 좋다.", NO_TRACE],
    "l2": ["필드별 정확도: 질의 파서가 읽은 기간·지역·분류 등이 사람 라벨과 같은 문항 비율. all_fields = 모든 필드가 맞은 비율.",
           "목록 필드 P/R: P가 낮으면 질문에 없는 조건을 지어냈고, R이 낮으면 질문의 조건을 놓쳤다.", NO_TRACE],
    "ablation": ["조합별로 슬롯 필터를 켜고 끄며 정답 표가 필터에서 살아남는지(filter recall) 본다. recall이 떨어지는 슬롯이 해로운 슬롯이다.",
                 "후보 중앙값: 필터 뒤 남은 표 수. 작을수록 검색이 쉬워지지만 recall이 떨어지면 안 된다.", NO_TRACE],
    "l012": ["L0(메타데이터) → L1 필터(정답 슬롯 / 파서 슬롯) → L2(질의 이해) 순서로 한 페이지에 모았다. 각 섹션 아래에 그 계층의 운영 지표가 있다.", NO_TRACE],
    "l1": ["Hit@k: 정답 표가 상위 k개 안에 든 문항 비율. MRR: 첫 정답 순위의 역수 평균. 둘 다 높을수록 좋다.",
           "LangGraph 검색이면 '실패 노드' 표에서 정답 표를 어느 노드가 밀어냈는지와 노드 경로를 본다.",
           "trace: 문항마다 execute_tool search_tables 아래 검색 노드의 실제 지연(워터폴)과 노드별 지연 표."],
    "l3": ["에이전트 그래프 한 턴에 정답 표를 준 채로(oracle 검색) summarize_stat의 숫자를 원본 Parquet 셀과 대조한다. PASS 비율이 높을수록 좋다.",
           "'실패 문항별 원인'의 span 경로에서 굵은 테두리가 실패로 판정한 단계다.",
           "trace: span 이름별 지연 표로 시간이 쓰인 곳을, 문항별 워터폴로 한 문항의 실행을 본다(실시간 계측, 서비스와 같은 모양)."],
    "e2e": ["서비스 에이전트 그래프 한 턴(검색 → summarize_stat)을 돌린다. 실패를 검색 실패(실전 1에 넘긴 표가 다른 표)와 답 실패로 나눠 본다.",
            "span 경로와 trace 워터폴, Jaeger 링크로 문항별 실행을 따라간다."],
    "agent": ["대화 성공률: 모든 턴이 통과한 대화 비율(가장 중요한 지표). pass^k: k번 반복 실행이 모두 성공한 시나리오 비율(안정성).",
              "툴 선택: 툴 이름·순서가 정답과 정확히 같은 턴 비율. 빠진·불필요 호출은 툴별로 따로 센다.",
              "대화별 화면: 실패한 대화가 위에 펼쳐져 있다. 툴 칩에 마우스를 올리면 인자와 결과가 보인다.",
              "trace(워터폴): 턴 안의 노드(파랑)·툴(초록)·LLM 호출(주황) 시작과 길이. 빨간 테두리 = error span. --agent로 실제 실행한 run에만 있다."],
}
ROW_CAP = 200  # rows shown in the result table; problem rows first, the rest are in results.jsonl


def guide(layer: str) -> str:
    items = GUIDE.get(layer)
    return f'<div class="card guide"><h2>읽는 법</h2><ul>{"".join(f"<li>{esc(i)}</li>" for i in items)}</ul></div>' if items else ""


def _inline(text: str) -> str:
    out = esc(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", out)


def md_html(text: str) -> str:
    """report.md (headings, bullet lines, tables, paragraphs) as HTML; the ops section is drawn by ops_body instead."""
    text = text.split("\n## 운영 지표")[0]
    out, table, items = [], [], []

    def flush() -> None:
        if table:
            head, *body = table
            out.append("<table><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>" for row in body) + "</table>")
            table.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|"):
            if items:
                flush()
            cells = [c.strip() for c in s.strip("|").split("|")]
            if not all(set(c) <= set("-: ") for c in cells):
                table.append(cells)
            continue
        flush()
        if not s or s.startswith("<!--") or s.startswith("# "):
            continue
        if s.startswith("## "):
            out.append(f"<h3>{_inline(s[3:])}</h3>")
        elif s.startswith("- "):
            items.append(s[2:])
        else:
            out.append(f"<p>{_inline(s)}</p>")
    flush()
    return '<div class="card md">' + "".join(out) + "</div>"


def summary_tiles(layer: str, s: dict) -> str:
    """The one or two numbers a reader looks for first, per layer without a dedicated body."""
    tiles = []
    if layer == "l0":
        tiles = [tile(f"{s.get('n_tables', 0):,}", "표"), tile(f"{s.get('n_gold', 0):,}", "골든 정답 표")]
    elif layer == "l2" and (a := (s.get("metrics") or {}).get("all")):
        tiles = [tile(pct(a["all_fields"]) if a.get("all_fields") is not None else "—", f"모든 필드 정답 (n={a['n']})"),
                 *[tile(pct(a[f]), f) for f in ("regions", "period_start", "cycle") if a.get(f) is not None]]
    elif layer in ("ablation", "l1-gold", "l1-pred") and (c := (s.get("combos") or {})):
        tiles = [tile(pct(c[k]["filter_recall"]), f"filter recall · {k}") for k in ("none", "all") if k in c]
    return f'<div class="tiles">{"".join(tiles)}</div>' if tiles else ""


def _problem(r: dict) -> bool:
    return (r.get("passed") is False or r.get("verdict") not in (None, "PASS") or r.get("all_fields") == 0
            or (r.get("answerable") is True and r.get("filter_pass") == 0))


def generic_rows_table(rows: list[dict]) -> str:
    """Scalar columns of the rows, problem rows first, at most ROW_CAP rows."""
    if not rows:
        return ""
    cols = [k for k, v in rows[0].items() if k != "layer" and (v is None or isinstance(v, (str, int, float, bool)))][:10]
    shown = sorted(rows, key=lambda r: not _problem(r))[:ROW_CAP]
    cell = lambda v: f'<td class="num">{v:.1f}</td>' if isinstance(v, float) else f"<td>{esc('' if v is None else v)}</td>"  # noqa: E731
    body = "".join("<tr>" + "".join(cell(r.get(c)) for c in cols) + "</tr>" for r in shown)
    n_bad = sum(map(_problem, rows))
    more = f" · {ROW_CAP}행까지 표시, 전체는 results.jsonl" if len(rows) > ROW_CAP else ""
    return (f'<details class="card"><summary>행별 결과 ({len(rows):,}건, 주의 {n_bad:,}건 먼저{more})</summary><table><tr>'
            + "".join(f"<th>{esc(c)}</th>" for c in cols) + "</tr>" + body + "</table></details>")


def tool_chips(calls: list[dict]) -> str:
    out = []
    for c in calls:
        tip = f"인자: {json.dumps(c.get('args'), ensure_ascii=False)[:400]}\n결과: {json.dumps(c.get('result'), ensure_ascii=False, default=str)[:600]}"
        cls = "chip err" if c.get("status") == "error" else "chip"
        out.append(f'<span class="{cls}" title="{esc(tip)}">{"✕ " if c.get("status") == "error" else ""}{esc(c.get("name"))}</span>')
    return "".join(out) or '<span class="tools">툴 호출 없음</span>'


def waterfall(spans: list[dict]) -> str:
    if not spans:
        return '<div class="tools">span 없음 (oracle·재채점 run, 또는 --agent 실행 전 기록)</div>'
    total = max(s["start_ms"] + s["duration_ms"] for s in spans) or 1
    rows = "".join(
        f'<div class="r"><span class="n" style="padding-left:{8 * s.get("depth", 0)}px" title="{esc(s["name"])}">{esc(s["name"])}</span>'
        f'<span class="track"><span class="bar {esc(s.get("kind", "node"))}-k{" err" if s.get("status") == "error" else ""}" '
        f'style="left:{s["start_ms"] / total * 100:.2f}%;width:{s["duration_ms"] / total * 100:.2f}%" '
        f'title="{esc(s["name"])} · 시작 {s["start_ms"]:.0f} ms · {s["duration_ms"]:.0f} ms · {esc(s.get("status", "ok"))}"></span></span>'
        f'<span class="num">{s["duration_ms"]:.0f} ms</span></div>' for s in spans)
    return f'<div class="wf">{rows}</div>'


TRACE_CAP = 60  # question waterfalls shown; problem rows first, every trace stays in results.jsonl and Jaeger


def _p(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else 0.0


def traces_body(m: dict, rows: list[dict]) -> str:
    """l1 / l3 / e2e (T3-32): the live trace of each question, read back from the run's rows (trace_spans).

    1) where the time goes: per span name, how many, p50 / p95 and its share of all span time below the root;
    2) one waterfall per question (problem rows first), the same view as the agent layer's turns."""
    traced = [r for r in rows if r.get("trace_spans")]
    if not traced:
        return ('<div class="card"><h2>trace</h2><p class="note">이 run에는 trace가 없다 (재채점 run, 또는 T3-32 이전 run).</p></div>')
    ui = (m.get("jaeger") or {}).get("ui")
    by_name: dict[str, list[float]] = {}
    kinds: dict[str, str] = {}
    for r in traced:
        for sp in r["trace_spans"]:
            if sp.get("depth", 0) == 0 and sp.get("kind") == "turn":
                continue  # the root is the question itself
            by_name.setdefault(sp["name"], []).append(sp["duration_ms"])
            kinds[sp["name"]] = sp.get("kind", "node")
    total = sum(sum(v) for v in by_name.values()) or 1
    top = sorted(by_name.items(), key=lambda kv: -sum(kv[1]))[:20]
    summary = "".join(
        f'<tr><td>{esc(name)}</td><td>{esc(kinds[name])}</td><td class="num">{len(v)}</td>'
        f'<td class="num">{_p(v, 0.5):.0f}</td><td class="num">{_p(v, 0.95):.0f}</td><td class="num">{sum(v) / total:.0%}</td></tr>'
        for name, v in top)
    shown = sorted(traced, key=lambda r: not _problem(r))[:TRACE_CAP]
    falls = "".join(
        f'<div class="turn"><div class="tools"><b>{esc(r["qid"])}</b> · {esc(r.get("verdict") or ("놓침" if r.get("failure") else "-"))}'
        f' · {r.get("latency_ms", 0):.0f} ms · LLM {r.get("llm_calls", 0)}회 · {trace_link(ui, r.get("trace_id"))}'
        f'{" · " + esc(r.get("question", "")) if r.get("question") else ""}</div>{waterfall(r["trace_spans"])}</div>'
        for r in shown)
    return "".join([
        '<div class="card"><h2>trace — 시간이 어디서 쓰였나</h2>',
        f'<p class="note">문항 {len(traced)}개의 실시간 trace(서비스와 같은 콜백, T3-32). span 이름별 횟수·지연 p50·p95(ms)와 '
        '루트 아래 span 시간 합에서 차지하는 비중(중첩 span은 겹쳐 셈). 상위 20개.</p>',
        '<table><tr><th>span</th><th>종류</th><th>횟수</th><th>p50</th><th>p95</th><th>비중</th></tr>', summary, "</table></div>",
        f'<div class="card"><h2>trace — 문항별 워터폴 ({len(shown)}/{len(traced)}건, 문제 문항 먼저)</h2>',
        '<p class="note">노드(파랑)·툴(초록)·LLM 호출(주황)의 시작과 길이. 빨간 테두리 = error span. 나머지는 results.jsonl의 trace_spans와 Jaeger 링크.</p>',
        falls, "</div>",
    ])


def agent_body(m: dict, rows: list[dict]) -> str:
    from evals.evaluators.agent import ERRORS

    s = m["summary"]
    f = lambda v: "—" if v is None else pct(v)  # noqa: E731
    tiles = [tile(f(s["conversation_success"]), f"대화 성공률 ({s['n_conversations']}대화)"), tile(f(s["pass^k"]), f"pass^{s['repeats']}"),
             tile(f(s["turn_pass"]), f"턴 통과율 ({s['n_turns']}턴)"), tile(f(s["tool_selection"]), "툴 선택 정확도"),
             tile(f(s["param_accuracy"]), "파라미터 정확도"), tile(f(s["state_accuracy"]), f"상태(표 키) 정확도 (n={s['n_state']})"),
             tile(f(s["numeric_accuracy"]), f"최종 수치 정확도 (n={s['n_numeric']})"), tile(str(s["unsupported_final_number_turns"]), "근거 없는 숫자가 있는 턴")]
    tools = sorted(set(s["missing_calls"]) | set(s["extra_calls"]))
    calls = (hbars([f"{t} 빠짐" for t in tools] + [f"{t} 불필요" for t in tools],
                   [s["missing_calls"].get(t, 0) for t in tools] + [s["extra_calls"].get(t, 0) for t in tools])
             if tools else '<p class="note">빠진·불필요 호출 없음</p>')
    fields = list(s["param_by_field"])
    params = (hbar_groups(fields, [("정확도", "--series-1", [s["param_by_field"][k]["acc"] or 0 for k in fields])],
                          [s["param_by_field"][k]["n"] for k in fields]) if fields else '<p class="note">채점한 인자 없음</p>')
    errors = sorted(s["errors"].items(), key=lambda kv: -kv[1])
    err = (hbars([k for k, _ in errors], [v for _, v in errors]) + '<ul class="info">'
           + "".join(f"<li><b>{esc(k)}</b> {v}건 — {esc(ERRORS.get(k, ''))}</li>" for k, v in errors) + "</ul>"
           if errors else '<p class="note">오류 없음</p>')
    first = s["first_failed_turn"]
    first_chart = hbars([f"턴 {k}" for k in first], list(first.values())) if first else '<p class="note">실패한 대화 없음</p>'
    ui = (m.get("jaeger") or {}).get("ui")
    convs: dict[tuple, list[dict]] = {}
    for r in rows:
        convs.setdefault((r["sid"], r.get("run", 0)), []).append(r)
    order = sorted(convs.items(), key=lambda kv: all(t["passed"] for t in kv[1]))
    blocks = []
    for (sid, run_no), turns in order[:ROW_CAP]:
        ok = all(t["passed"] for t in turns)
        lat = sum(t.get("latency_ms", 0) for t in turns)
        tok = sum(t.get("input_tokens", 0) + t.get("output_tokens", 0) for t in turns)
        head = (f'<summary><span class="{"ok" if ok else "bad"}">{"✓ 성공" if ok else "✕ 실패"}</span> · {esc(sid)} #{run_no} · '
                f'{len(turns)}턴 · {lat:.0f} ms · 토큰 {tok:,}</summary>')
        body = "".join(
            f'<div class="turn"><div class="q">턴 {t["turn"]} · {esc(t["question"])}</div>'
            f'<div>{tool_chips(t.get("calls") or [])} <span class="tools">끝난 표 {esc(t.get("state_key") or "-")}</span></div>'
            f'<div class="a">{esc(t.get("answer") or "")}</div>'
            f'<div>{"<span class=\'chip ok\'>✓ 통과</span>" if t["passed"] else "".join(f"<span class=\'chip err\' title=\'{esc(ERRORS.get(e, e))}\'>{esc(e)}</span>" for e in t["errors"])}'
            f' <span class="tools">{t.get("latency_ms", 0):.0f} ms · LLM {t.get("llm_calls", 0)}회 · 토큰 {t.get("input_tokens", 0) + t.get("output_tokens", 0):,} · '
            f'{trace_link(ui, t.get("trace_id"))}</span></div>{waterfall(t.get("spans") or [])}</div>'
            for t in turns)
        blocks.append(f'<details class="conv"{"" if ok else " open"}>{head}<div class="turns">{body}</div></details>')
    more = f'<p class="note">{ROW_CAP}개 대화까지 표시(실패 먼저), 전체는 results.jsonl.</p>' if len(order) > ROW_CAP else ""
    return "".join([
        '<div class="tiles">', *tiles, "</div>",
        '<div class="grid2"><div class="card"><h2>빠진 · 불필요 호출 (툴별)</h2><p class="note">멀티쿼리 턴에서 빠진 요청, 같은 툴 반복 호출을 따로 센다.</p>', calls, "</div>",
        '<div class="card"><h2>인자 필드별 정확도</h2><p class="note">같은 툴의 k번째 호출과 정답을 필드별로 비교(목록은 집합).</p>', params, "</div></div>",
        '<div class="grid2"><div class="card"><h2>턴 오류</h2>', err, "</div>",
        '<div class="card"><h2>처음 실패한 턴</h2><p class="note">실패한 대화가 몇 번째 턴에서 처음 무너졌는지. 0 = 첫 턴.</p>', first_chart, "</div></div>",
        f'<h2 style="margin-top:12px">대화별 결과와 trace ({len(convs)}대화)</h2>', more, *blocks,
    ])


def golden_label(m: dict) -> str:
    g = m.get("golden") or {}
    if "sha256" in g:
        return f'골든 {esc(g["sha256"][:12])}'
    return " · ".join(f'골든 {esc(k)} {esc(v["sha256"][:12])}' for k, v in g.items() if isinstance(v, dict) and "sha256" in v)


def history_of(layer: str) -> list[dict]:
    return [{**h, "total_tokens": (h.get("input_tokens") or 0) + (h.get("output_tokens") or 0)}
            for h in read_history() if h["layer"] == layer]


def l012_body(m: dict, rows: list[dict], report: str) -> str:
    """One section per part (l0, l1-gold, l1-pred, l2): its report.md part, its ops and its rows."""
    parts = dict(p.split(" -->\n", 1) for p in report.split("<!-- ")[1:])
    out = []
    for name in m["summary"]:
        mine = [r for r in rows if r.get("layer") == name]
        out += [f'<h2 style="margin-top:28px">{esc(LAYER_TITLES.get(name, name))}</h2>', summary_tiles(name, m["summary"][name]),
                md_html(parts.get(name, "")), ops_body(m["ops"][name], mine, history_of(name)), generic_rows_table(mine)]
    return "".join(out)


def render(run: Path, questions: dict[str, str] | None = None) -> str:
    m = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(l) for l in (run / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    report = (run / "report.md").read_text(encoding="utf-8") if (run / "report.md").exists() else ""
    for r in rows:
        if "qid" in r:
            r.setdefault("question", (questions or {}).get(r["qid"], ""))
    layer = m["layer"]
    models = sorted({x for o in (m["ops"].values() if layer == "l012" else [m["ops"]]) for x in o.get("models", [])})
    model = ", ".join(models) or ("summarize_stat" if layer in ("l3", "e2e") else "LLM 호출 없음")
    target = {"e2e": f'그래프 {m.get("agent") or "backend.app.main:build_agent"} · 모델 {model}',
              "l3": f'그래프 {m.get("agent") or "evals.runners.multiturn_dev:build (oracle 검색)"} · 모델 {model}',
              "l1": f'검색기 {m["retriever"]}',
              "agent": f'에이전트 {m.get("agent") or m.get("source") or "-"} · 모델 {model}'}.get(layer, f"모델 {model}")
    meta = (f'{esc(m["run_id"])} · {esc(target)} · {"split " + esc(m["split"]) + " · " if m.get("split") else ""}'
            f'{golden_label(m)} · tables.jsonl {esc(m["source_snapshot"]["tables.jsonl"][:12])} · '
            f'git {esc(m["git"][:8])}{" (dirty)" if m.get("git_dirty") else ""}')
    if layer == "l012":
        body, ops, table = l012_body(m, rows, report), "", ""
    else:
        dedicated = {"l1": l1_body, "l3": l3_body, "e2e": e2e_body, "agent": agent_body}
        body = (dedicated[layer](m, rows) if layer in dedicated else summary_tiles(layer, m["summary"]) + md_html(report))
        body += failures_body(m, rows) + search_failures_body(m, rows)
        if layer in ("l1", "l3", "e2e"):
            body += traces_body(m, rows)
        ops = ops_body(m["ops"], rows, history_of(layer))
        table = rows_table(layer, rows) if layer in ("l1", "l3", "e2e") else "" if layer == "agent" else generic_rows_table(rows)
    title = LAYER_TITLES.get(layer, layer)
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{esc(title)} · {esc(m['stage'])}</title><style>{CSS}</style></head>"
            f'<body><div class="viz-root"><main><h1>{esc(title)} · {esc(m["stage"])}</h1><div class="meta">{meta}</div>'
            f"{guide(layer)}{body}{ops}{table}</main></div></body></html>")


def questions_of(layer: str) -> dict[str, str]:
    from evals.schemas import DATASETS

    path = DATASETS / ("retrieval_golden.jsonl" if layer == "l1" else "calculation_golden.jsonl")
    if not path.exists():
        return {}
    return {d["qid"]: d["question"] for d in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if d}


def write(run: Path) -> Path:
    layer = json.loads((run / "manifest.json").read_text(encoding="utf-8"))["layer"]
    out = run / "report.html"
    out.write_text(render(run, questions_of(layer)), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render report.html for an evals run folder.")
    parser.add_argument("--run", type=Path, required=True)
    print(f"-> {write(parser.parse_args(argv).run)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
