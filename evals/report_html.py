"""report.html for one run folder: score tiles and charts, slot-level table, ops metrics and their trend over runs.

    python -m evals.report_html --run evals/reports/<run_id>        # re-render; run_eval calls write() itself

Self-contained (inline SVG and CSS, no CDN), so it opens offline. Light and dark follow the OS. Every mark has a
native hover tooltip (<title>). Colors follow the dataviz reference palette: categorical slots 1-3 for Hit@1/5/10,
status colors (with icon and label) for PASS/PARTIAL/FAIL, one blue for single-series bars.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from evals.evaluators.calculation import ERROR_INFO
from evals.ops import read_history
from evals.trace import STAGES

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
             tile(pct(a.get("slot_recall@5", 0)), "SlotRecall@5"), tile(pct(a.get("complete@3", 0)), "Complete@3"),
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
        cls, icon = ("ok", "✓") if rank <= 5 else ("warn", "△")
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
        '<div class="card"><h2>슬롯별 채점 (복합 질문)</h2><p class="note">✓ 5위 안 · △ 6~10위 · ✕ 10위 밖. 오른쪽은 SlotRecall@10.</p>',
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
    labels, counts = histogram([r["latency_ms"] for r in rows if "latency_ms" in r])
    lat = vbars(labels, counts, ["--series-1"] * len(labels)) if labels else ""
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


def trace_link(ui: str | None, trace_id: str | None) -> str:
    return f'<a href="{esc(ui)}/trace/{esc(trace_id)}" target="_blank">trace</a>' if ui and trace_id else "-"


def timeline(r: dict) -> str:
    """The question's span path as chips: ✕ = error span, bold = the span blamed for the failure."""
    blamed = (r.get("failure") or {}).get("span")
    if nodes := r.get("search_spans"):  # LangGraph search (T2-36): one chip per node
        chips = [(s["name"], s["status"], s["duration_ms"]) for s in nodes]
    else:
        chips = [("retrieve", "ok" if r.get("right_table", True) else "error", None)] if "right_table" in r else []
    if "verdict" in r:  # L3 / e2e: the tool and its Phase 1 spans
        chips.append(("summarize_stat", "error" if blamed == "tool.summarize_stat" else "ok", None))
        chips += [(s["name"], s["status"], s["duration_ms"]) for s in r.get("spans") or []]
    out = []
    for name, status, ms in chips:
        cls = "chip" + (" err" if status == "error" else "") + (" blame" if name in (blamed, f"tool.{name}") else "")
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
    jaeger = (f'Jaeger: <a href="{esc(ui)}/search?service=kosis-evals" target="_blank">kosis-evals 서비스</a>' if ui
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


def render(run: Path, questions: dict[str, str] | None = None) -> str:
    m = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(l) for l in (run / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for r in rows:
        r.setdefault("question", (questions or {}).get(r["qid"], ""))
    history = [{**h, "total_tokens": (h.get("input_tokens") or 0) + (h.get("output_tokens") or 0)}
               for h in read_history() if h["layer"] == m["layer"]]
    layer = m["layer"]
    model = ", ".join(m["ops"]["models"]) or "summarize_stat"
    target = f'검색기 {m["retriever"]} · 모델 {model}' if layer == "e2e" else f'검색기 {m["retriever"]}' if layer == "l1" else f"모델 {model}"
    meta = (f'{esc(m["run_id"])} · {esc(target)} · {"split " + esc(m["split"]) + " · " if layer == "l1" else ""}'
            f'골든 {esc(m["golden"]["sha256"][:12])} · tables.jsonl {esc(m["source_snapshot"]["tables.jsonl"][:12])} · '
            f'git {esc(m["git"][:8])}{" (dirty)" if m.get("git_dirty") else ""}')
    body = {"l1": l1_body, "e2e": e2e_body}.get(layer, l3_body)(m, rows)
    title = {"l1": "L1 검색", "l3": "L3 계산", "e2e": "L4 종단 간 (e2e)"}.get(layer, layer)
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{esc(title)} · {esc(m['stage'])}</title><style>{CSS}</style></head>"
            f'<body><div class="viz-root"><main><h1>{esc(title)} · {esc(m["stage"])}</h1><div class="meta">{meta}</div>'
            f"{body}{failures_body(m, rows)}{search_failures_body(m, rows)}{ops_body(m['ops'], rows, history)}{rows_table(layer, rows)}</main></div></body></html>")


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
