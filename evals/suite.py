"""End-to-end eval suite (T3-21): pre-check, run every layer once in order, compare with a baseline run of the same conditions.

    python -m evals.suite --stage T3-07-react --llm mock                       # path check at no cost
    python -m evals.suite --stage T3-07-react --baseline T3-06-parity          # HCX, compared with matching runs
    python -m evals.suite --stage T3-07-react --only l1-graph e2e --agent kosis_agent.graph:build_agent --scenarios <file>

Each run is `python -m evals.run_eval` with stage `<prefix>-<run name>` (its own folder, report.html and ops_history
line). A run whose needs are not met (no Chroma, no checkpoint, no key, ...) is skipped with the reason; a run that
fails is recorded and the suite goes on. The suite folder `evals/reports/<time>-<prefix>-suite/` holds suite.md,
suite.html and suite.json: the checks, each run's status and folder, and per run its headline metrics next to the
baseline run with the same conditions (layer, retriever, golden hash, split, k, live vs regrade, llm).
Exit code 1 when a run failed (a skipped run is not a failure). Reads no .env: set the values in the shell.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import json
import os
import socket
from pathlib import Path
from typing import Callable

from evals.schemas import DATASETS, ROOT

REPORTS = ROOT / "evals" / "reports"
V2 = ["--golden", str(DATASETS / "retrieval_golden_v2.jsonl"), "--pool", str(DATASETS / "retrieval_pool_v2.csv")]

# name, run_eval args, needs (keys of check()), what it measures; the order is the order they run in
RUNS: list[tuple[str, list[str], set[str], str]] = [
    ("l0", ["--layer", "l0"], {"data"}, "메타데이터 채움률 (모델·서버 없음)"),
    ("l0-load", ["--layer", "l0", "--retriever", "chroma"], {"data", "chroma"}, "+ Chroma 컬렉션 적재 일치"),
    ("l1-bm25-record", ["--layer", "l1", "--retriever", "bm25-record"], {"data"}, "BM25 검색 (서버 없음)"),
    ("l1-v2-bm25-record", ["--layer", "l1", "--retriever", "bm25-record", *V2], {"data", "v2"}, "BM25, 골든 v2"),
    ("l1-hybrid", ["--layer", "l1", "--retriever", "hybrid+cond"], {"data", "chroma"}, "임베딩 + BM25 + 조건 필터"),
    ("l1-staged", ["--layer", "l1", "--retriever", "staged"], {"data", "chroma", "rerank"}, "+ 1차 리랭커"),
    ("l1-graph", ["--layer", "l1", "--retriever", "graph:staged"], {"data", "chroma", "rerank"}, "실제 LangGraph 검색 그래프"),
    ("ablation", ["--layer", "ablation", "--retriever", "chroma", "--split", "dev"], {"data", "chroma"}, "슬롯 필터 on/off별 검색"),
    ("l2-v1", ["--layer", "l2", "--split", "all"], {"data"}, "질의 파서 (규칙)"),
    ("l2-v2", ["--layer", "l2", "--split", "all", "--query-golden", str(DATASETS / "query_golden_v2.jsonl")], {"data", "v2"}, "질의 파서, 골든 v2"),
    ("l3", ["--layer", "l3"], {"data", "llm"}, "정답 표로 summarize_stat 실호출"),
    ("e2e", ["--layer", "e2e"], {"data", "chroma", "rerank", "llm"}, "검색 그래프 1위 표 → summarize_stat"),
    ("agent", ["--layer", "agent"], {"agent"}, "멀티턴 시나리오 → 에이전트 (T3-06)"),
]
WARN_ONLY = {"sigungu"}  # checks no run needs: shown as ⚠, never skip a run
# Conditions a baseline must share (manifest fields); llm missing on old live runs means hcx
MATCH = ("layer", "retriever", "split", "top_k", "agent")


def check(llm: str, agent: str | None, scenarios: Path | None) -> dict[str, tuple[bool, str]]:
    """{need: (ok, detail)} for this machine and shell. Key values are never shown (set / not set only)."""
    from evals.config import data_dir, search_config

    data = data_dir()
    spec, _ = search_config()
    out: dict[str, tuple[bool, str]] = {}
    missing = [p.name for p in (data / "tables.jsonl", data / "values", DATASETS / "retrieval_golden.jsonl", DATASETS / "query_golden.jsonl",
                                DATASETS / "calculation_golden.jsonl") if not p.exists()]
    out["data"] = (not missing, f"없음: {', '.join(missing)}" if missing else f"{data} · evals/datasets")
    # Warning only (no run needs it): without it parse reads no 시군구 names, so those questions' regions drop.
    # Runs stay comparable with baselines made without it; build it to change that (results change).
    out["sigungu"] = ((data / "sigungu.json").exists(), "data/sigungu.json 있음" if (data / "sigungu.json").exists() else
                      "경고: data/sigungu.json 없음 → 시군구가 든 질문의 regions를 못 읽음 (만들기: "
                      "python -m kosis_rag_build.cli sigungu --data-dir <제공 데이터>; 만들면 기준 run과 조건이 달라짐)")
    v2 = [p.name for p in (DATASETS / "retrieval_golden_v2.jsonl", DATASETS / "retrieval_pool_v2.csv",
                           DATASETS / "query_golden_v2.jsonl") if not p.exists()]
    out["v2"] = (not v2, f"없음: {', '.join(v2)}" if v2 else "골든 v2 파일 있음")
    out["chroma"] = _chroma(spec)
    rerank = spec.rerank_ckpt
    out["rerank"] = ((True, f"RERANK_CKPT {rerank}") if rerank and Path(rerank).exists()
                     else (False, f"RERANK_CKPT 파일 없음: {rerank}" if rerank else "RERANK_CKPT 미설정"))
    out["llm"] = ((True, "mock (호출 없음)") if llm == "mock" else
                  (True, "HCX_API_KEY 설정됨") if os.environ.get("HCX_API_KEY") else (False, "HCX_API_KEY 미설정 (또는 --llm mock)"))
    out["agent"] = ((True, f"{agent} · {scenarios}") if agent and scenarios and scenarios.exists()
                    else (False, "--agent module:function와 --scenarios 파일 필요"))
    return out


def _chroma(spec) -> tuple[bool, str]:
    host, port, name = spec.chroma.host, spec.chroma.port, spec.chroma.collection
    if not name:
        return False, "CHROMA_COLLECTION 미설정"
    if spec.embed_ckpt and not Path(spec.embed_ckpt).exists():
        return False, f"EMBED_CKPT 파일 없음: {spec.embed_ckpt}"
    try:
        socket.create_connection((host, port), timeout=2).close()
    except OSError:
        return False, f"Chroma {host}:{port} 응답 없음 (Docker·실전 2 chroma 실행 확인)"
    try:
        from kosis_rag.adapters.chroma import chroma_client

        chroma_client(spec.chroma).get_collection(name)
    except Exception as e:  # noqa: BLE001 - any failure means the collection cannot be searched
        return False, f"컬렉션 {name} 열기 실패: {type(e).__name__}"
    return True, f"{host}:{port} · 컬렉션 {name}"


def argv_for(name: str, args: list[str], llm: str, opts: argparse.Namespace) -> list[str]:
    """run_eval args of one run: the stage, and the llm / interval / retriever / agent options it takes."""
    out = [*args, "--stage", f"{opts.stage}-{name}"]
    if name in ("l3", "e2e"):
        out += ["--llm", llm, "--interval", str(opts.interval if opts.interval is not None else (0 if llm == "mock" else 5))]
    if name == "e2e":  # stage 2 HCX picks only on a live run; a mock run checks the path without any model call
        out += ["--retriever", "graph:staged-hcx" if llm == "hcx" else "graph:staged"]
    if name == "agent":
        out += ["--agent", opts.agent, "--scenarios", str(opts.scenarios), "--repeat", str(opts.repeat), "--split", "all"]
    return out


def metrics(m: dict) -> dict[str, float | int | str | None]:
    """The few numbers a reader compares first, per layer (from manifest summary)."""
    s, layer = m["summary"], m["layer"]
    if layer == "l1":
        a = s["metrics"]["all"]
        return {"Hit@1": a["hit@1"], "Hit@5": a["hit@5"], "Hit@10": a["hit@10"], "MRR": a["rr"], "답 없음 F1": s["no_answer"]["f1"]}
    if layer == "l2":
        a = s["metrics"]["all"]
        return {"모든 필드": a["all_fields"], "regions": a.get("regions"), "period_start": a.get("period_start"), "cycle": a.get("cycle")}
    if layer == "l0":
        out = {"표": s["n_tables"], "measure 미매핑(골든)": s["gold"]["measure_unmapped"], "dimension 미매핑(골든)": s["gold"]["dimension_unmapped"]}
        if "load" in s:
            out["적재 일치"] = "일치" if s["load"]["match"] else "불일치"
        return out
    if layer == "ablation":
        c = s["combos"]
        return {"filter recall (all)": c["all"]["filter_recall"], "후보 중앙값 (all)": c["all"]["candidates_median"],
                "Hit@5 (all)": c["all"].get("hit@5"), "Hit@5 (none)": c["none"].get("hit@5")}
    if layer in ("l3", "e2e"):
        v = s["verdicts"]["all"]
        n = sum(v.values()) or 1
        out = {"PASS 비율": v.get("PASS", 0) / n, "PASS": v.get("PASS", 0), "FAIL": v.get("FAIL", 0)}
        if layer == "e2e":
            out["검색 1위 = 정답 표"] = s["top1_right"]
        return out
    if layer == "agent":
        return {k: s[k] for k in ("conversation_success", "pass^k", "turn_pass", "tool_selection", "param_accuracy",
                                  "state_accuracy", "numeric_accuracy", "unsupported_final_number_turns")}
    return {}


def conditions(m: dict) -> dict:
    """What a baseline must share: MATCH fields, golden hash(es), live vs regrade, llm (missing on a live run = hcx),
    and for l0 whether the Chroma load was checked (the retriever field is not recorded for l0)."""
    live = m["layer"] in ("l3", "e2e") and not m.get("from_run")
    golden = m.get("golden") or {}
    return {**{k: m.get(k) for k in MATCH}, "golden": json.dumps(golden.get("sha256") or {k: v.get("sha256") for k, v in golden.items()}, sort_keys=True),
            "regrade": bool(m.get("from_run")), "llm": (m.get("llm") or "hcx") if live else None,
            "load_check": "load" in (m.get("summary") or {}) if m["layer"] == "l0" else None}


def manifests(where: list[str]) -> list[tuple[Path, dict]]:
    """Run folders named directly, or every run whose stage starts with a prefix, oldest first."""
    found = []
    for w in where:
        path = Path(w)
        folders = [path] if (path / "manifest.json").exists() else [
            d for d in sorted(REPORTS.iterdir()) if (d / "manifest.json").exists() and d.name.split("-", 2)[-1].startswith(w)]
        for d in folders:
            found.append((d, json.loads((d / "manifest.json").read_text(encoding="utf-8"))))
    return found


def baseline_for(m: dict, pool: list[tuple[Path, dict]]) -> tuple[Path, dict] | None:
    """The latest run in pool with the same conditions as m (never m's own folder)."""
    want = conditions(m)
    same = [(d, b) for d, b in pool if b.get("run_id") != m.get("run_id") and conditions(b) == want]
    return same[-1] if same else None


def run(opts: argparse.Namespace, main: Callable[[list[str]], Path] | None = None,
        checks: dict[str, tuple[bool, str]] | None = None) -> dict:
    """Run the selected runs; returns the suite record (also written as suite.json / .md / .html)."""
    if main is None:
        from evals.run_eval import main
    checks = checks if checks is not None else check(opts.llm, opts.agent, opts.scenarios)
    pool = manifests(opts.baseline or [])
    rows = []
    for name, args, needs, what in RUNS:
        if opts.only and name not in opts.only:
            continue
        row = {"name": name, "what": what, "status": "skipped", "reason": "", "run": None, "metrics": {}, "baseline": None, "base_metrics": {}}
        if unmet := [n for n in sorted(needs) if not checks[n][0]]:
            row["reason"] = " · ".join(checks[n][1] for n in unmet)
            rows.append(row)
            continue
        try:
            with contextlib.redirect_stdout(io.StringIO()):  # each report is in its folder; keep the console to the suite
                folder = main(argv_for(name, args, opts.llm, opts))
        except SystemExit as e:  # argparse and the layers' own checks
            row.update(status="failed", reason=f"SystemExit: {e.code}")
        except Exception as e:  # noqa: BLE001 - one failing run must not stop the others
            row.update(status="failed", reason=f"{type(e).__name__}: {e}")
        else:
            m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            row.update(status="mock" if m.get("llm") == "mock" else "ok", run=str(folder), metrics=metrics(m))
            if base := baseline_for(m, pool):
                row.update(baseline=str(base[0]), base_metrics=metrics(base[1]))
            elif opts.baseline:
                row["reason"] = "조건 같은 기준 run 없음"
        rows.append(row)
        print(f"{row['status']:>7}  {name:<18} {row['reason'] or row['run']}")
    return {"stage": opts.stage, "llm": opts.llm, "baseline": opts.baseline, "checks": {k: list(v) for k, v in checks.items()}, "runs": rows}


def _fmt(v) -> str:
    return "—" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)


def _delta(a, b) -> str:
    if isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool):
        return f"{b - a:+d}"
    return f"{b - a:+.3f}" if isinstance(a, (int, float)) and isinstance(b, (int, float)) else ("같음" if a == b else "다름")


def markdown(suite: dict) -> str:
    lines = [f"# 종단 간 평가 스위트 — {suite['stage']}", "",
             f"- llm `{suite['llm']}`" + (" (경로 확인용: L3·e2e 점수는 점수가 아님)" if suite["llm"] == "mock" else "")
             + f" · 기준 {', '.join(suite['baseline']) if suite['baseline'] else '없음'}", "",
             "## 사전 점검", "", "| 항목 | 상태 | 내용 |", "| --- | --- | --- |",
             *[f"| {k} | {'✓' if ok else '⚠' if k in WARN_ONLY else '✕'} | {d} |" for k, (ok, d) in suite["checks"].items()], "",
             "## 실행", "", "| run | 무엇을 재나 | 상태 | run 폴더 · 사유 |", "| --- | --- | --- | --- |",
             *[f"| {r['name']} | {r['what']} | {r['status']} | {Path(r['run']).name if r['run'] else ''}{' · ' if r['run'] and r['reason'] else ''}{r['reason']} |"
               for r in suite["runs"]], ""]
    for r in suite["runs"]:
        if not r["metrics"]:
            continue
        lines += [f"## {r['name']}", "", f"- 기준: {Path(r['baseline']).name if r['baseline'] else '없음'}", "",
                  "| 지표 | 기준 | 이번 | 차이 |", "| --- | --- | --- | --- |"]
        for k, v in r["metrics"].items():
            b = r["base_metrics"].get(k) if r["baseline"] else None
            lines.append(f"| {k} | {_fmt(b)} | {_fmt(v)} | {_delta(b, v) if r['baseline'] else ''} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def html_page(suite: dict, md: str) -> str:
    from evals import report_html as h

    links = "".join(f'<li><a href="../{h.esc(Path(r["run"]).name)}/report.html">{h.esc(r["name"])}</a> · {h.esc(r["status"])}</li>'
                    for r in suite["runs"] if r["run"])
    guide = ('<div class="card guide"><h2>읽는 법</h2><ul><li>사전 점검에서 ✕인 항목이 필요한 run은 건너뛴다(상태 skipped, 사유 칸).</li>'
             '<li>run마다 기준 run(같은 계층·검색기·골든·split·k·재채점 여부·llm)과 핵심 지표를 나란히 둔다. 차이는 이번 − 기준.</li>'
             '<li>상태 mock = 모델 없이 경로만 확인한 run. L3·e2e 점수는 점수로 쓰지 않는다.</li>'
             '<li>run 이름을 누르면 그 run의 report.html(결과·운영 지표·trace)이 열린다.</li></ul></div>')
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>평가 스위트 · {h.esc(suite["stage"])}</title><style>{h.CSS}</style></head><body><div class="viz-root"><main>'
            f'<h1>종단 간 평가 스위트 · {h.esc(suite["stage"])}</h1>{guide}'
            f'<div class="card"><h2>run 리포트</h2><ul class="info">{links or "<li>실행된 run 없음</li>"}</ul></div>'
            f"{h.md_html(md)}</main></div></body></html>")


def write(suite: dict) -> Path:
    out = REPORTS / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{suite['stage']}-suite"
    out.mkdir(parents=True)
    md = markdown(suite)
    (out / "suite.json").write_text(json.dumps(suite, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "suite.md").write_text(md, encoding="utf-8")
    (out / "suite.html").write_text(html_page(suite, md), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-check, run L0-L4 (+agent) in order and compare with baseline runs.")
    parser.add_argument("--stage", required=True, help="prefix of every run's stage, e.g. T3-07-react")
    parser.add_argument("--llm", choices=("hcx", "mock"), default="hcx", help="l3, e2e: mock checks the path at no cost")
    parser.add_argument("--baseline", nargs="+", help="stage prefixes or run folders to compare with (same conditions only)")
    parser.add_argument("--only", nargs="+", choices=[r[0] for r in RUNS], help="run only these")
    parser.add_argument("--interval", type=float, help="l3, e2e: seconds between model calls (default 5 for hcx, 0 for mock)")
    parser.add_argument("--agent", help="agent: module:function returning a compiled graph")
    parser.add_argument("--scenarios", type=Path, default=DATASETS / "agent_scenarios.jsonl", help="agent: scenarios file")
    parser.add_argument("--repeat", type=int, default=1, help="agent: runs per scenario (pass^k)")
    opts = parser.parse_args(argv)
    suite = run(opts)
    out = write(suite)
    print(f"-> {out / 'suite.html'}")
    return 1 if any(r["status"] == "failed" for r in suite["runs"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
