"""Pieces several evaluation tasks share: L3 grading rows, verdict table, report sections, owner lines."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from evals import trace
from evals.evaluators import calculation
from evals.schemas import ROOT, CalcItem, load_jsonl

MOCK_NOTE = " · **llm mock: 경로 확인용, 점수 아님** (모델 대신 코드 템플릿이 답함)"
OPS_FIELDS = ("latency_ms", "llm_calls", "input_tokens", "output_tokens", "model", "failed", "retried")
TRACE_FIELDS = ("spans", "retrieve_ms", "search_spans")


def load_sigungu() -> dict[str, list[str]]:
    """The 시군구 -> 시도 dictionary parse_query needs (T2-41: without it parse reads no 시군구 and L2 regions drop)."""
    from kosis_rag.adapters.files import load_sigungu as load

    from evals.config import data_dir

    return load(data_dir() / "sigungu.json")


def pct(hit: int, n: int) -> str:
    return f"{hit}/{n} ({hit / n:.1%})" if n else "대상 없음"


OWNERS = {"data": "데이터 (메타데이터·조건 파서: T2-14·T2-29·T2-30)", "ai_search": "AI 파이프라인 — 검색·답 없음 판정 (T2-06·07·09)",
          "ai_answer": "AI 파이프라인 — summarize_stat 계산·문장 (실전 1)", "ai_query": "AI 파이프라인 — summarize_stat 질의 분석 (StatQuery)"}


def add(found: dict[str, list[str]], owner: str, what: str, qids: list[str]) -> None:
    """One owner line: what failed, how many, and up to five example qids."""
    if qids:
        found.setdefault(owner, []).append(f"{what} {len(qids)} ({', '.join(qids[:5])}{' …' if len(qids) > 5 else ''})")


def owner_section(found: dict[str, list[str]]) -> str:
    """'Who fixes it': a task's owners() lines grouped by the team that owns the failing part."""
    if not found:
        return ""
    lines = ["## 고칠 담당", ""]
    for owner, items in found.items():
        lines += [f"- **{OWNERS[owner]}**", *(f"  - {i}" for i in items)]
    return "\n".join(lines) + "\n\n"


def answer_owners(rows: list[dict], e2e: bool) -> dict[str, list[str]]:
    """L3 / L4 owners: wrong table goes to search, wrong answer and wrong 연도·지역 to summarize_stat."""
    found: dict[str, list[str]] = {}
    add(found, "ai_search", "검색 1위가 다른 표", [r["qid"] for r in rows if r.get("cause") == "retrieval"])
    add(found, "ai_answer", "정답 표인데 답 틀림" if e2e else "정답 표 주고도 답 틀림",
        [r["qid"] for r in rows if r["verdict"] != "PASS" and r.get("cause", "answer") == "answer"])
    add(found, "ai_query", "답의 연도가 질문과 다름", [r["qid"] for r in rows if r.get("year_ok") is False])
    add(found, "ai_query", "답의 지역이 질문과 다름", [r["qid"] for r in rows if r.get("region_ok") is False])
    return found


def summarize_stat_target(op: dict) -> str:
    """ops_history target of a run that calls summarize_stat: its models, else the tool name."""
    return ",".join(op["models"]) or "summarize_stat"


def ops_section(op: dict, title: str = "운영 지표") -> str:
    c = op["cost"]
    cost = f"{c['total']:.2f} {c['currency']}" if c["total"] is not None else f"단가 미설정 ({', '.join(c['unpriced_models']) or '토큰 없음'}; evals/pricing.json)"
    return "\n".join(["", f"## {title}", "",
        f"- Latency: p50 {op['latency_ms']['p50']:.1f} ms · p95 {op['latency_ms']['p95']:.1f} ms · 평균 {op['latency_ms']['mean']:.1f} ms",
        f"- Token Usage: 입력 {op['tokens']['input']:,} · 출력 {op['tokens']['output']:,} (호출당 {op['tokens']['per_call_mean']:.0f}) · LLM 호출 {op['llm_calls']}",
        f"- Cost: {cost}",
        f"- Tool Failure Rate: {op['tool_failure_rate']:.1%} ({op['tool_calls']}회 중) · 재시도 비율 {op['retry_rate']:.1%}",
    ]) + "\n"


def failure_section(rows: list[dict], exported: bool) -> str:
    """Stage counts and one line per non-PASS question: the span it failed in and why (evals.trace.blame)."""
    failed = [r for r in rows if r.get("failure")]
    counts = Counter(r["failure"]["stage"] for r in failed)
    lines = ["", "## 실패 단계 (trace)", "",
             "- 판정 순서: " + " > ".join(f"{k}({v})" for k, v in trace.STAGES.items()) + " — 첫 번째로 맞는 규칙 하나 (plans/T2-15.md)",
             "- 단계별: " + (", ".join(f"{trace.STAGES[k]} {counts[k]}" for k in trace.STAGES if counts[k]) or "실패 없음"),
             f"- Jaeger: {'trace 전송함 (' + trace.JAEGER_UI + ')' if exported else 'OTEL_EXPORTER_OTLP_ENDPOINT 미설정, 전송 안 함'}"]
    if failed:
        lines += ["", "| qid | 판정 | 단계 | span | 사유 | 거친 span |", "| --- | --- | --- | --- | --- | --- |"]
        for r in failed:
            f = r["failure"]
            path = " → ".join(s["name"] + ("✕" if s["status"] == "error" else "") for s in r.get("spans") or []) or "-"
            lines.append(f"| {r['qid']} | {r['verdict']} | {f['label']} | {f['span'] or '-'} | {f['reason'].replace('|', '/')} | {path} |")
    return "\n".join(lines) + "\n"


def search_failure_section(rows: list[dict], exported: bool) -> str:
    """L1 graph runs: misses per graph node and one line per miss (evals.trace.blame_search)."""
    failed = [r for r in rows if r.get("failure")]
    counts = Counter(r["failure"]["span"] or "-" for r in failed)
    lines = ["", "## 실패 노드 (LangGraph trace)", "",
             "- 판정: Hit@k 놓침 → 정답 표를 top k 밖으로 마지막으로 내린 노드(처음부터 밖이면 retrieve), 답 없음 오판 → gate (plans/T2-36.md)",
             "- 노드별: " + (", ".join(f"{k} {v}" for k, v in counts.most_common()) or "실패 없음") + f" (합 {len(failed)})",
             f"- Jaeger: {'trace 전송함 (' + trace.JAEGER_UI + ')' if exported else 'OTEL_EXPORTER_OTLP_ENDPOINT 미설정, 전송 안 함'}"]
    if failed:
        lines += ["", "| qid | qtype | 실패 | 노드 | 사유 | 거친 노드 |", "| --- | --- | --- | --- | --- | --- |"]
        for r in failed:
            f = r["failure"]
            path = " → ".join(s["name"] + ("✕" if s["status"] == "error" else "") for s in r["search_spans"]) or "-"
            lines.append(f"| {r['qid']} | {r['qtype']} | {f['label']} | {f['span'] or '-'} | {f['reason'].replace('|', '/')} | {path} |")
    return "\n".join(lines) + "\n"


def grade(items: list[CalcItem], answers: list[dict]) -> list[dict]:
    """L3 grading of each answer against the golden table's cells, with the ops fields kept per row."""
    rows = []
    for item, a in zip(items, answers):
        cells = calculation.read_cells(ROOT / "data" / "values" / f"{item.table_key}.parquet")
        g = calculation.grade(item, a["answer"], cells, a["error"])
        rows.append({**g.model_dump(), "table_key": item.table_key, **{k: a[k] for k in OPS_FIELDS + TRACE_FIELDS if k in a},
                     "answer": a["answer"].model_dump(mode="json") if a["answer"] else None})
    return rows


def verdict_table(rows: list[dict]) -> tuple[dict, Counter, list[str]]:
    """Verdict counts per kind, error counts and the markdown table both L3 and L4 reports show."""
    by_kind: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_kind["all"][r["verdict"]] += 1
        by_kind[r["kind"]][r["verdict"]] += 1
    errors = Counter(e for r in rows for e in r["errors"])
    lines = ["| kind | n | PASS | PARTIAL | FAIL |", "| --- | --- | --- | --- | --- |"]
    for k in ["all", "answerable", "not_answerable", "clarify", "default_allowed"]:
        v = by_kind.get(k, Counter())
        lines.append(f"| {k} | {sum(v.values())} | {v['PASS']} | {v['PARTIAL']} | {v['FAIL']} |")
    return {k: dict(v) for k, v in by_kind.items()}, errors, lines


def calc_items(paths: list[Path]) -> list[CalcItem]:
    """L3/L4 items from one or more golden files (Phase 1 file, T2-31 diverse file); qids must not repeat."""
    items = [i for p in paths for i in load_jsonl(p, CalcItem)]
    qids = Counter(i.qid for i in items)
    if dupes := [q for q, n in qids.items() if n > 1]:
        raise SystemExit(f"duplicate qids across {[str(p) for p in paths]}: {dupes[:5]}")
    return items
