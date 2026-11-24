"""Phase 2 검색(L1) 로컬 채점: 저장된 L1 run을 다시 채점함. 검색은 다시 하지 않음.

    python scripts/p2eval/grade_l1.py --run evals/reports/<L1 run 폴더> [--golden <골든 jsonl>] [--split dev|test|all]
                                      [--pool evals/datasets/retrieval_pool.csv] [--out <report.md>] [--json <summary.json>]
    python scripts/p2eval/grade_l1.py --selftest

기존 L1 채점(evals/tasks/l1_retrieval.py)과 다른 점 (2026-11-19 사용자 명세):
- 삭제: 임계점 타우(top_score < tau → 답 없음). 답 없음 판정은 검색 그래프가 실제로 거절했는지로만 봄(2차 LLM 리랭커가 대신함).
- 추가: 전체 정답률 = 전체 문항 중 맞게 처리한 비율.
    답 있는 문항: 거절하지 않았고 1위 표(Hit@1)가 정답 표 중 하나
    답 없는 문항: 표를 찾지 않음(거절)
- 그 외(Hit@k, MRR, slot recall, complete@k, unjudged@10, 답 없음 P/R/F1, 사유별 거절, 오류 수, 지연)는 기존 정의 유지.
  Hit@k·MRR은 기존처럼 거절과 무관하게 순위만 봄. 거절을 반영한 값은 "답 있는 문항 정답률"(= Hit@1 거절 반영).

거절 판정(우선순위): gate span의 route == "refuse" → 없으면 2차 LLM이 0개 고름(호출 오류 제외) 또는 검색 결과 0개.
실행 오류(error) 행은 오답으로 셈.
표준 라이브러리만 씀. evals·phase2를 불러오지 않음. 질문 문장은 읽지도 쓰지도 않음(qid만 출력).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

KS = (1, 2, 3, 5, 10)


# ---------- 입력 ----------

def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_golden(path: Path) -> dict[str, dict]:
    """qid -> {split, qtype, answerable, no_answer_reason, required}. required = [[OR 묶음], ...] (묶음끼리 AND)."""
    out = {}
    for d in read_jsonl(path):
        out[d["qid"]] = {"split": d.get("split"), "qtype": d.get("qtype") or "-",
                         "answerable": d.get("answerable", True) is not False,
                         "no_answer_reason": d.get("no_answer_reason"),
                         "required": [list(s) for s in (d.get("required") or [])]}
    return out


def judged_keys(path: Path | None) -> dict[str, set[str]] | None:
    if path is None or not path.exists():
        return None
    judged: dict[str, set[str]] = defaultdict(set)
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            judged[row["qid"]].add(row["key"])
    return judged


# ---------- 판정 ----------

def gate_route(row: dict) -> str | None:
    for s in row.get("search_spans") or []:
        if s.get("name") == "gate":
            return (s.get("attributes") or {}).get("route")
    return None


def refused(row: dict) -> bool:
    """검색이 표를 내지 않았나(거절). 타우는 쓰지 않음."""
    route = gate_route(row)
    if route is not None:
        return route == "refuse"
    if row.get("stage2_picked_n") == 0 and not row.get("stage2_error"):
        return True
    return not row.get("keys")


def gold_keys(required: list[list[str]]) -> set[str]:
    return {k for slot in required for k in slot}


def rank_metrics(required: list[list[str]], keys: list[str], judged: set[str] | None) -> dict:
    """기존 evaluators/retrieval.score_item과 같은 정의(답 있는 문항)."""
    gold = gold_keys(required)
    rank = next((i for i, k in enumerate(keys, 1) if k in gold), None)
    row = {"rank": rank, "rr": 1 / rank if rank else 0.0}
    for k in KS:
        top = set(keys[:k])
        covered = sum(1 for slot in required if top & set(slot))
        row[f"hit@{k}"] = int(rank is not None and rank <= k)
        row[f"complete@{k}"] = int(covered == len(required))
        row[f"slot_recall@{k}"] = covered / len(required) if required else 0.0
    if judged is not None:
        top = keys[:10]
        row["unjudged@10"] = sum(1 for key in top if key not in judged and key not in gold) / max(len(top), 1)
    return row


def grade_row(row: dict, g: dict, judged: set[str] | None) -> dict:
    keys = list(row.get("keys") or [])
    ref = refused(row)
    error = bool(row.get("error"))
    out = {"qid": row["qid"], "split": g["split"] or row.get("split"), "qtype": g["qtype"],
           "answerable": g["answerable"], "no_answer_reason": g["no_answer_reason"],
           "refused": ref, "error": error, "latency_ms": row.get("latency_ms"),
           "llm_calls": row.get("llm_calls") or 0}
    if g["answerable"]:
        out.update(rank_metrics(g["required"], keys, judged))
        out["correct"] = int(not error and not ref and bool(keys) and keys[0] in gold_keys(g["required"]))
        out["outcome"] = ("error" if error else "wrong_refusal" if ref else "hit" if out["correct"] else "wrong_table")
    else:
        out["correct"] = int(not error and ref)
        out["outcome"] = "error" if error else "refused" if ref else "answered_unanswerable"
    return out


# ---------- 집계 ----------

def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def pct(values: list[float], q: float) -> float | None:
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    pos = (len(v) - 1) * q / 100
    lo, hi = int(pos), min(int(pos) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def prf(rows: list[dict]) -> dict:
    """답 없음 클래스(양성 = 답 없는 문항)의 P/R/F1. 예측 = 실제 거절."""
    tp = sum(r["refused"] and not r["answerable"] for r in rows)
    fp = sum(r["refused"] and r["answerable"] for r in rows)
    fn = sum(not r["refused"] and not r["answerable"] for r in rows)
    p = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": p, "recall": rc, "f1": 2 * p * rc / (p + rc) if p + rc else 0.0, "tp": tp, "fp": fp, "fn": fn}


def accuracy(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["answerable"]]
    una = [r for r in rows if not r["answerable"]]
    return {"n": len(rows), "correct": sum(r["correct"] for r in rows), "acc": mean([r["correct"] for r in rows]),
            "n_ans": len(ans), "acc_ans": mean([r["correct"] for r in ans]),
            "n_una": len(una), "acc_una": mean([r["correct"] for r in una])}


RANK_COLS = ["hit@1", "hit@3", "hit@5", "hit@10", "rr", "slot_recall@5", "slot_recall@10", "complete@2", "complete@3",
             "complete@10", "unjudged@10"]


def rank_table(rows: list[dict]) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["answerable"]:
            groups["all"].append(r)
            groups[r["qtype"]].append(r)
    return {name: {"n": len(m), **{c: mean([x[c] for x in m if c in x]) for c in RANK_COLS if any(c in x for x in m)}}
            for name, m in groups.items()}


def summarize(graded: list[dict]) -> dict:
    by_split: dict[str, list[dict]] = defaultdict(list)
    by_qtype: dict[str, list[dict]] = defaultdict(list)
    by_reason: dict[str, list[dict]] = defaultdict(list)
    outcomes: dict[str, int] = defaultdict(int)
    for r in graded:
        by_split[r["split"] or "-"].append(r)
        by_qtype[r["qtype"]].append(r)
        if not r["answerable"]:
            by_reason[r["no_answer_reason"] or "-"].append(r)
        outcomes[r["outcome"]] += 1
    lat = [r["latency_ms"] for r in graded]
    return {
        "accuracy": {"all": accuracy(graded), **{k: accuracy(v) for k, v in sorted(by_split.items())}},
        "accuracy_by_qtype": {k: accuracy(v) for k, v in sorted(by_qtype.items())},
        "rank": rank_table(graded),
        "no_answer": prf(graded),
        "refusal_by_reason": {k: {"n": len(v), "refused": sum(r["refused"] for r in v)} for k, v in sorted(by_reason.items())},
        "outcomes": dict(sorted(outcomes.items())),
        "errors": sum(r["error"] for r in graded),
        "latency_ms": {"p50": pct(lat, 50), "p95": pct(lat, 95)},
        "llm_calls_mean": mean([r["llm_calls"] for r in graded]),
    }


# ---------- 리포트 ----------

def f3(x) -> str:
    return "-" if x is None else f"{x:.3f}"


def report(s: dict, meta: dict, graded: list[dict]) -> str:
    acc = s["accuracy"]
    lines = [f"# Phase 2 검색 로컬 채점 — {meta['run']}", "",
             f"- retriever `{meta.get('retriever')}`, top_k {meta.get('top_k')}, 골든 `{meta['golden']}`, split `{meta['split']}`",
             "- 타우 없음. 거절 = 검색 그래프 gate가 refuse(없으면 2차 LLM 0개 또는 결과 0개)", "",
             "## 전체 정답률", "",
             "| 구분 | 문항 | 정답 | 전체 정답률 | 답 있는 문항 (Hit@1, 거절 반영) | 답 없는 문항 (거절) |", "| --- | --- | --- | --- | --- | --- |"]
    for name, a in acc.items():
        lines.append(f"| {name} | {a['n']} | {a['correct']} | {f3(a['acc'])} | {f3(a['acc_ans'])} ({a['n_ans']}) | "
                     f"{f3(a['acc_una'])} ({a['n_una']}) |")
    lines += ["", "| qtype | 문항 | 전체 정답률 |", "| --- | --- | --- |"]
    lines += [f"| {k} | {a['n']} | {f3(a['acc'])} |" for k, a in s["accuracy_by_qtype"].items()]
    lines += ["", "결과 유형: " + " · ".join(f"{k} {v}" for k, v in s["outcomes"].items()),
              "(hit 정답 · wrong_table 다른 표 1위 · wrong_refusal 답 있는데 거절 · refused 답 없음 거절 · "
              "answered_unanswerable 답 없는데 표 냄 · error 실행 오류)", "",
              "## 순위 지표 (답 있는 문항, 거절과 무관하게 순위만)", ""]
    cols = [c for c in RANK_COLS if c in s["rank"].get("all", {})]
    lines += ["| qtype | n | " + " | ".join("MRR" if c == "rr" else c for c in cols) + " |", "|" + " --- |" * (len(cols) + 2)]
    for name in ["all"] + sorted(k for k in s["rank"] if k != "all"):
        m = s["rank"][name]
        lines.append(f"| {name} | {m['n']} | " + " | ".join(f3(m.get(c)) for c in cols) + " |")
    na = s["no_answer"]
    lines += ["", "## 답 없음 (실제 거절 기준)", "",
              f"- P {f3(na['precision'])} · R {f3(na['recall'])} · F1 {f3(na['f1'])} (tp {na['tp']}, fp {na['fp']}, fn {na['fn']})",
              "- 사유별 거절: " + (", ".join(f"{k} {v['refused']}/{v['n']}" for k, v in s["refusal_by_reason"].items())
                               or "답 없음 문항 없음"),
              f"- 실행 오류 {s['errors']}건 · 지연 p50 {f3(s['latency_ms']['p50'])} ms, p95 {f3(s['latency_ms']['p95'])} ms · "
              f"LLM 호출 평균 {f3(s['llm_calls_mean'])}", "",
              "## 틀린 문항 (qid)", ""]
    for kind in ("wrong_table", "wrong_refusal", "answered_unanswerable", "error"):
        q = [r["qid"] for r in graded if r["outcome"] == kind]
        if q:
            lines.append(f"- {kind} ({len(q)}): " + ", ".join(q))
    return "\n".join(lines) + "\n"


# ---------- 실행 ----------

def grade_run(run: Path, golden: Path | None, split: str, pool: Path | None) -> tuple[dict, str, list[dict]]:
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    if golden is None:
        golden = Path(manifest["golden"]["path"].replace("\\", "/"))
    gold = load_golden(golden)
    judged = judged_keys(pool)
    rows = read_jsonl(run / "results.jsonl")
    missing = [r["qid"] for r in rows if r["qid"] not in gold]
    if missing:
        raise SystemExit(f"골든에 없는 qid {len(missing)}개 (예: {missing[:3]}): --golden을 확인")
    graded = [grade_row(r, gold[r["qid"]], judged.get(r["qid"], set()) if judged is not None else None) for r in rows]
    graded = [r for r in graded if split == "all" or r["split"] == split]
    lost = sum(1 for q, g in gold.items() if (split == "all" or g["split"] == split)) - len(graded)
    s = summarize(graded)
    s["golden_items_not_in_run"] = lost
    meta = {"run": run.name, "retriever": manifest.get("retriever"), "top_k": manifest.get("top_k"),
            "golden": str(golden), "split": split}
    text = report(s, meta, graded)
    if lost:
        text = f"> 주의: 골든 {lost}문항이 run에 없음(정답률 분모에서 빠짐)\n\n" + text
    return s, text, graded


def selftest() -> int:
    gold = {"A": {"split": "dev", "qtype": "x", "answerable": True, "no_answer_reason": None, "required": [["T1", "T2"]]},
            "B": {"split": "dev", "qtype": "x", "answerable": True, "no_answer_reason": None, "required": [["T1"]]},
            "C": {"split": "dev", "qtype": "no_answer", "answerable": False, "no_answer_reason": "no_item", "required": []},
            "D": {"split": "test", "qtype": "no_answer", "answerable": False, "no_answer_reason": "out_of_scope", "required": []},
            "E": {"split": "test", "qtype": "x", "answerable": True, "no_answer_reason": None, "required": [["T9"]]}}
    gate = lambda route: [{"name": "gate", "attributes": {"route": route}}]
    rows = [{"qid": "A", "keys": ["T2", "T5"], "search_spans": gate("found"), "top_score": 0.0},     # 정답(대체 표), 낮은 점수여도 타우 없음
            {"qid": "B", "keys": ["T5", "T1"], "search_spans": gate("found")},                     # 다른 표 1위
            {"qid": "C", "keys": ["T3"], "search_spans": gate("refuse")},                          # 답 없음 거절(정답)
            {"qid": "D", "keys": ["T4"], "stage2_picked_n": 2},                                    # gate 없음, 표 냄(오답)
            {"qid": "E", "keys": ["T9"], "stage2_picked_n": 0}]                                    # 정답 1위지만 거절(오답)
    g = [grade_row(r, gold[r["qid"]], None) for r in rows]
    assert [x["correct"] for x in g] == [1, 0, 1, 0, 0], g
    assert [x["outcome"] for x in g] == ["hit", "wrong_table", "refused", "answered_unanswerable", "wrong_refusal"]
    assert g[1]["hit@2"] == 1 and g[4]["hit@1"] == 1  # 순위 지표는 거절과 무관
    s = summarize(g)
    assert s["accuracy"]["all"]["correct"] == 2 and abs(s["accuracy"]["all"]["acc"] - 0.4) < 1e-9
    assert s["accuracy"]["dev"]["acc"] == 2 / 3 and s["accuracy"]["test"]["acc"] == 0.0
    na = s["no_answer"]
    assert (na["tp"], na["fp"], na["fn"]) == (1, 1, 1)
    assert refused({"keys": [], "search_spans": []}) and refused({"keys": ["T"], "stage2_picked_n": 0})
    assert not refused({"keys": ["T"], "stage2_picked_n": 0, "stage2_error": "401"})  # 호출 실패는 거절 아님
    err = grade_row({"qid": "A", "keys": ["T1"], "error": "boom", "search_spans": gate("found")}, gold["A"], None)
    assert err["correct"] == 0 and err["outcome"] == "error"
    report(s, {"run": "selftest", "golden": "-", "split": "all"}, g)
    print("selftest ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", type=Path, help="L1 run 폴더 (results.jsonl, manifest.json)")
    ap.add_argument("--golden", type=Path, help="골든 jsonl. 없으면 manifest의 골든 경로")
    ap.add_argument("--split", default="all", choices=["dev", "test", "all"])
    ap.add_argument("--pool", type=Path, help="판정 풀 csv(unjudged@10용, 선택)")
    ap.add_argument("--out", type=Path, help="report.md 저장 경로. 없으면 화면에 출력")
    ap.add_argument("--json", type=Path, help="요약 json 저장 경로(선택)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.run is None:
        ap.error("--run 이 필요함")
    s, text, _ = grade_run(args.run, args.golden, args.split, args.pool)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        a = s["accuracy"]["all"]
        print(f"전체 정답률 {a['acc']:.3f} ({a['correct']}/{a['n']}) -> {args.out}")
    else:
        sys.stdout.write(text)
    if args.json:
        args.json.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
