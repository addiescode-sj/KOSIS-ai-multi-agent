"""What the search graph really refused: the `gate` span of each L1 row, not the evaluator's tau rule (KOS-193).

The same logic as scripts/answerability/pipeline_report.py (row_of / summarize give the same numbers), but each row
carries its own `answerable` / `no_answer_reason` (the L1 rows do), so no golden file is read. Stdlib only and no
evals import: `python evals/evaluators/gate.py` runs the selftest.

Per results row (qid, qtype, answerable, no_answer_reason, keys, hit@1, stage2_picked_n, stage2_error, stage2_calls,
latency_ms, search_spans):
    refused   the gate span's attributes.route == "refuse"; a row without a gate span falls back to `not keys`
    refuser   classifier  the `answerable` span says refused (rerank2 skipped, gate: not_answerable)
              stage2      stage 2 ran without error and picked nothing (stage2_picked_n == 0, no stage2_error)
              no_docs     the search returned no tables
              other       none of these (only meaningful for refused rows: counted in checks)
Positive class = not answerable (`answerable` False; missing or None = answerable). No question text is read out.
"""

from __future__ import annotations

from collections import Counter

REFUSERS = ("classifier", "stage2", "no_docs", "other")
REFUSER_KO = {"classifier": "분류기(classifier)", "stage2": "2차 LLM(stage2)", "no_docs": "후보 없음(no_docs)",
              "other": "기타(other)"}


def span(r: dict, name: str) -> dict | None:
    """The first search span of that node name in a results row, or None."""
    return next((s for s in r.get("search_spans") or [] if s.get("name") == name), None)


def attrs(s: dict | None) -> dict:
    return (s or {}).get("attributes") or {}


def has_gate(results: list[dict]) -> bool:
    """Any results row has a gate span (a graph search with the gate node)."""
    return any(span(r, "gate") is not None for r in results)


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile (q in 0..100); None for no values."""
    v = sorted(values)
    if not v:
        return None
    pos = (len(v) - 1) * q / 100
    lo, hi = int(pos), min(int(pos) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (pos - lo), 3)


def lat(values: list[float]) -> dict:
    return {"n": len(values), "p50": percentile(values, 50), "p95": percentile(values, 95)}


def spread(values: list[float]) -> dict:
    return {"n": len(values), "min": min(values) if values else None,
            "median": percentile(values, 50) if values else None, "max": max(values) if values else None}


def row_of(r: dict) -> dict:
    """One row: the final decision, who refused, and what the answerable / rerank2 spans recorded."""
    keys = r.get("keys") or []
    gate, ans, r2 = span(r, "gate"), span(r, "answerable"), span(r, "rerank2")
    refused = attrs(gate).get("route") == "refuse" if gate else not keys
    a = attrs(ans)
    if a.get("refused") is True:
        refuser = "classifier"
    elif r.get("stage2_picked_n") == 0 and not r.get("stage2_error"):
        refuser = "stage2"
    elif not keys:
        refuser = "no_docs"
    else:
        refuser = "other"
    return {"qid": r["qid"], "qtype": r.get("qtype") or "-", "answerable": r.get("answerable") is not False,
            "reason": r.get("no_answer_reason") or "-", "hit1": bool(r.get("hit@1")), "refused": refused,
            "refuser": refuser, "no_gate": gate is None, "has_answerable": ans is not None,
            "classifier_refused": a.get("refused") is True, "top_score": a.get("top_score"),
            "theta": a.get("theta"), "n_scored": a.get("n_scored") or 0,
            "stage2_called": (r.get("stage2_calls") or 0) > 0, "stage2_error": bool(r.get("stage2_error")),
            "stage2_skipped": attrs(r2).get("skipped") == "answer_refused", "latency_ms": r.get("latency_ms")}


def final_block(rows: list[dict]) -> dict:
    tp = sum(r["refused"] and not r["answerable"] for r in rows)
    fp = sum(r["refused"] and r["answerable"] for r in rows)
    fn = sum(not r["refused"] and not r["answerable"] for r in rows)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {"refused_right": tp, "false_refusals": fp, "missed": fn, "precision": round(prec, 4),
            "recall": round(rec, 4), "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0}


def summarize(rows: list[dict]) -> dict:
    """pipeline_report.summarize over row_of rows, plus the false refusal / missed qid lists and has_gate /
    has_classifier."""
    ans = [r for r in rows if r["answerable"]]
    una = [r for r in rows if not r["answerable"]]
    by_refuser: dict[str, Counter] = {}
    for r in rows:
        if r["refused"]:
            by_refuser.setdefault(r["refuser"], Counter())["answerable" if r["answerable"] else "unanswerable"] += 1
    reasons = sorted({r["reason"] for r in una})
    total = Counter(r["reason"] for r in una)
    recall_by_reason = {"final": {k: [sum(r["refused"] for r in una if r["reason"] == k), total[k]] for k in reasons}}
    for who in REFUSERS:
        recall_by_reason[who] = {k: [sum(r["refused"] and r["refuser"] == who for r in una if r["reason"] == k),
                                     total[k]] for k in reasons}
    lat_all = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    lat_cls = [r["latency_ms"] for r in rows if r["latency_ms"] is not None and r["classifier_refused"]]
    lat_rest = [r["latency_ms"] for r in rows if r["latency_ms"] is not None and not r["classifier_refused"]]
    thetas = sorted({r["theta"] for r in rows if r["theta"] is not None})
    scored = [r for r in rows if r["n_scored"] > 0]
    return {
        "n": len(rows), "n_answerable": len(ans), "n_unanswerable": len(una),
        "final": final_block(rows),
        "by_refuser": {k: {"answerable": c["answerable"], "unanswerable": c["unanswerable"]}
                       for k, c in sorted(by_refuser.items(), key=lambda kv: REFUSERS.index(kv[0]))},
        "recall_by_reason": recall_by_reason,
        "hit1_answerable_kept": round(sum(r["hit1"] and not r["refused"] for r in ans) / len(ans), 4) if ans else None,
        "hit1_answerable_run": round(sum(r["hit1"] for r in ans) / len(ans), 4) if ans else None,
        "stage2": {"called": sum(r["stage2_called"] for r in rows),
                   "skipped_by_classifier": sum(r["stage2_skipped"] for r in rows),
                   "errors": sum(r["stage2_error"] for r in rows)},
        "latency_ms": {"all": lat(lat_all), "classifier_refused": lat(lat_cls), "rest": lat(lat_rest)},
        "classifier": {"theta": thetas[0] if len(thetas) == 1 else (thetas or None), "n_scored": len(scored),
                       "refused": sum(r["classifier_refused"] for r in rows),
                       "top_score": {"answerable": spread([r["top_score"] for r in scored if r["answerable"]
                                                           and r["top_score"] is not None]),
                                     "unanswerable": spread([r["top_score"] for r in scored if not r["answerable"]
                                                             and r["top_score"] is not None])}},
        "checks": {"classifier_refused_but_stage2_called": sum(r["classifier_refused"] and r["stage2_called"]
                                                               for r in rows),
                   "gate_refuse_without_refuser": sum(r["refused"] and r["refuser"] == "other" for r in rows),
                   "no_gate_span": sum(r["no_gate"] for r in rows),
                   "rows_without_answerable_span": sum(not r["has_answerable"] for r in rows)},
        "false_refusals": [{"qid": r["qid"], "qtype": r["qtype"], "refuser": r["refuser"], "top_score": r["top_score"]}
                           for r in ans if r["refused"]],
        "missed": [{"qid": r["qid"], "reason": r["reason"], "top_score": r["top_score"]}
                   for r in una if not r["refused"]],
        "has_gate": any(not r["no_gate"] for r in rows),
        "has_classifier": any(r["has_answerable"] for r in rows),
    }


def num(v, digits: int) -> str:
    """A number at that many decimals; "-" for None (a list of thetas is joined)."""
    if v is None:
        return "-"
    if isinstance(v, list):
        return ", ".join(num(x, digits) for x in v) or "-"
    return f"{v:.{digits}f}"


def refused_by(s: dict, who: str) -> tuple[int, int]:
    """(unanswerable, answerable) refusals of one refuser."""
    c = s["by_refuser"].get(who, {})
    return c.get("unanswerable", 0), c.get("answerable", 0)


def render_md(title: str, info: list[str], s: dict, tau_line: str | None = None) -> str:
    """gate_report.md: the graph's real refusals (gate) of one run, in Korean notes. info: bullet lines as given."""
    f = s["final"]
    lines = [f"# 실제 거절 내역 (gate) — {title}", "", *info,
             "- 이 파일은 그래프가 실제로 내린 거절 판정(gate 노드)임. report.md의 \"답 없음 (tau=…)\" 줄은 평가기가 "
             "사후에 적용한 점수 기준이며 파이프라인은 쓰지 않음.", "",
             "## 1. 최종 판정", "",
             f"- 문항 {s['n']}개 · 답 있음 {s['n_answerable']} · 답 없음 {s['n_unanswerable']}", "",
             "| 구분 | 건수 |", "| --- | --- |",
             f"| 답 없음을 거절(맞음) | {f['refused_right']}/{s['n_unanswerable']} |",
             f"| 답 있음을 거절(오거절) | {f['false_refusals']}/{s['n_answerable']} |",
             f"| 답 없음을 놓침 | {f['missed']} |", "",
             f"- P {f['precision']:.3f} · R {f['recall']:.3f} · F1 {f['f1']:.3f}", "",
             "## 2. 누가 거절했는가", "",
             "| 거절 주체 | 답 없음을 거절 | 답 있음을 거절(오거절) |", "| --- | --- | --- |"]
    lines += [f"| {REFUSER_KO[who]} | {refused_by(s, who)[0]} | {refused_by(s, who)[1]} |" for who in REFUSERS]
    if not s.get("has_classifier"):
        lines += ["", "- 이번 실행은 분류기가 꺼져 있었음 (answerable span 없음)."]
    rr = s["recall_by_reason"]
    lines += ["", "## 3. 사유별 거절", ""]
    if rr["final"]:
        lines += ["| 사유 | 최종 | 분류기 | 2차 LLM | 후보 없음 |", "| --- | --- | --- | --- | --- |"]
        lines += [f"| {k} | " + " | ".join("{}/{}".format(*rr[col][k])
                                           for col in ("final", "classifier", "stage2", "no_docs")) + " |"
                  for k in rr["final"]]
    else:
        lines.append("- 답 없음 문항 없음.")
    lines += ["", "## 4. 답 있는 문항 Hit@1", "",
              f"- run_eval 값(거절 무시): {num(s['hit1_answerable_run'], 3)}",
              f"- 거절 반영(거절하면 틀림): {num(s['hit1_answerable_kept'], 3)}", ""]
    st, la = s["stage2"], s["latency_ms"]
    lines += ["## 5. 2차 LLM 호출과 지연", "",
              f"- 2차 LLM 호출 {st['called']}건 · 분류기 거절로 건너뜀 {st['skipped_by_classifier']}건 · "
              f"호출 오류 {st['errors']}건", "",
              "| 구간 | n | p50 (ms) | p95 (ms) |", "| --- | --- | --- | --- |"]
    lines += [f"| {name} | {la[key]['n']} | {num(la[key]['p50'], 0)} | {num(la[key]['p95'], 0)} |"
              for name, key in (("전체", "all"), ("분류기 거절", "classifier_refused"), ("나머지", "rest"))]
    lines.append("")
    if s.get("has_classifier"):
        c = s["classifier"]
        lines += ["## 6. 분류기 점수", "",
                  f"- theta {num(c['theta'], 3)} · 점수 매긴 문항 {c['n_scored']} · 분류기 거절 {c['refused']}", "",
                  "| 구분 | n | 최소 | 중앙값 | 최대 |", "| --- | --- | --- | --- | --- |"]
        lines += [f"| {name} | {t['n']} | {num(t['min'], 3)} | {num(t['median'], 3)} | {num(t['max'], 3)} |"
                  for name, t in (("답 있음", c["top_score"]["answerable"]),
                                  ("답 없음", c["top_score"]["unanswerable"]))]
        lines.append("")
    lines += ["## 7. 문항 목록", "", "### 오거절(답 있는데 거절)", ""]
    if s["false_refusals"]:
        lines += ["| qid | qtype | 거절 주체 | 분류기 최고 점수 |", "| --- | --- | --- | --- |"]
        lines += [f"| {x['qid']} | {x['qtype']} | {REFUSER_KO[x['refuser']]} | {num(x['top_score'], 3)} |"
                  for x in s["false_refusals"]]
    else:
        lines.append("- 없음")
    lines += ["", "### 놓침(답 없는데 안내)", ""]
    if s["missed"]:
        lines += ["| qid | 사유 | 분류기 최고 점수 |", "| --- | --- | --- |"]
        lines += [f"| {x['qid']} | {x['reason']} | {num(x['top_score'], 3)} |" for x in s["missed"]]
    else:
        lines.append("- 없음")
    ch = s["checks"]
    lines += ["", "## 8. 참고와 점검", ""]
    if tau_line:
        lines.append(f"- 참고: τ 규칙(파이프라인에는 없음) {tau_line}")
    lines += [f"- {k}: {v}" for k, v in ch.items()]
    lines.append("- classifier_refused_but_stage2_called, gate_refuse_without_refuser는 0이어야 함. "
                 "no_gate_span은 gate span 없이 `keys` 유무로 대신 판정한 문항 수임.")
    return "\n".join(lines).rstrip("\n") + "\n"


def summary_line(s: dict) -> str:
    """One report.md bullet: the gate's P/R/F1 and who refused (total, of which answerable)."""
    f = s["final"]

    def one(name: str, k: str) -> str:
        total = sum(refused_by(s, k))
        return f"{name} {total}(오거절 {refused_by(s, k)[1]})" if total else f"{name} 0"

    who = [one(name, k) for name, k in (("분류기", "classifier"), ("2차 LLM", "stage2"), ("후보 없음", "no_docs"))]
    if sum(refused_by(s, "other")):  # shown only when present: gate refusals nobody explains (see checks)
        who.append(one("기타", "other"))
    return (f"- 답 없음 (gate, 그래프의 실제 거절): P {f['precision']:.3f} · R {f['recall']:.3f} · F1 {f['f1']:.3f} "
            f"(tp {f['refused_right']}, fp {f['false_refusals']}, fn {f['missed']}) · 거절 주체: {', '.join(who)}"
            " · 자세한 내용은 gate_report.md")


def fixture() -> list[dict]:
    """Fake L1 results rows (pipeline_report.selftest's cases, each row carrying its own labels)."""
    def spans(route=None, refused=None, top=None, skipped=None, theta=-1.0):
        out = []
        if refused is not None:
            out.append({"name": "answerable", "attributes": {k: v for k, v in (
                ("top_score", top), ("refused", refused), ("theta", theta), ("n_scored", 5)) if v is not None}})
        if skipped:
            out.append({"name": "rerank2", "attributes": {"skipped": skipped}})
        if route:
            out.append({"name": "gate", "attributes": {"route": route}})
        return out

    def row(qid, answerable, reason, keys, hit, picked, calls, ms, sp, **kw):
        return {"qid": qid, "split": "dev", "qtype": "single", "answerable": answerable, "no_answer_reason": reason,
                "keys": keys, "hit@1": hit, "stage2_picked_n": picked, "stage2_calls": calls, "latency_ms": ms,
                "search_spans": sp, **kw}

    return [
        # classifier refuses an unanswerable row (rerank2 skipped, no stage 2 call)
        row("c1", False, "no_period", ["X"], 0, None, None, 100.0, spans("refuse", True, -3.0, "answer_refused")),
        # classifier refuses an answerable row (false refusal) that still had hit@1
        row("c2", True, None, ["A"], 1, None, None, 120.0, spans("refuse", True, -2.0, "answer_refused")),
        # classifier passes, stage 2 picks nothing: refused by stage 2
        row("s1", False, "no_item", ["Y"], 0, 0, 1, 900.0, spans("refuse", False, 1.0)),
        # passes both, answerable (field missing), hit
        {k: v for k, v in row("k1", True, None, ["A"], 1, 1, 1, 1000.0, spans("found", False, 2.0)).items()
         if k not in ("answerable", "no_answer_reason")},
        # passes both, unanswerable: missed; stage 2 failed (an error is not a refusal)
        row("m1", False, "no_item", ["Z"], 0, 0, 1, 800.0, spans("found", False, 0.5), stage2_error="boom"),
        # no tables, no gate span -> fallback; no answerable span either
        row("n1", False, "out_of_scope", [], 0, None, None, 50.0, []),
        # gate refused but nobody did (inconsistent): "other"; and a classifier refusal with a stage 2 call (bug)
        row("o1", True, None, ["A"], 1, 2, 1, 700.0, spans("refuse", False, 3.0)),
        row("b1", False, "no_period", ["X"], 0, 1, 1, 600.0, spans("refuse", True, -4.0)),
    ]


def selftest() -> None:
    res = fixture()
    assert has_gate(res) and not has_gate([r for r in res if r["qid"] == "n1"]) and not has_gate([])
    rows = [row_of(r) for r in res]
    by = {r["qid"]: r for r in rows}
    assert [by[q]["refuser"] for q in ("c1", "c2", "s1", "k1", "m1", "n1", "o1", "b1")] == \
        ["classifier", "classifier", "stage2", "other", "other", "no_docs", "other", "classifier"]
    assert by["n1"]["refused"] and by["n1"]["no_gate"] and not by["m1"]["refused"] and by["k1"]["answerable"]
    assert by["k1"]["reason"] == "-" and by["c1"]["reason"] == "no_period"
    s = summarize(rows)
    assert (s["n"], s["n_answerable"], s["n_unanswerable"]) == (8, 3, 5)
    assert s["final"] == {"refused_right": 4, "false_refusals": 2, "missed": 1, "precision": 0.6667, "recall": 0.8,
                          "f1": round(2 * (4 / 6) * 0.8 / (4 / 6 + 0.8), 4)}, s["final"]
    assert s["by_refuser"] == {"classifier": {"answerable": 1, "unanswerable": 2},
                               "stage2": {"answerable": 0, "unanswerable": 1},
                               "no_docs": {"answerable": 0, "unanswerable": 1},
                               "other": {"answerable": 1, "unanswerable": 0}}, s["by_refuser"]
    rr = s["recall_by_reason"]
    assert rr["final"] == {"no_item": [1, 2], "no_period": [2, 2], "out_of_scope": [1, 1]}, rr
    assert rr["classifier"] == {"no_item": [0, 2], "no_period": [2, 2], "out_of_scope": [0, 1]}
    assert rr["stage2"]["no_item"] == [1, 2] and rr["no_docs"]["out_of_scope"] == [1, 1]
    assert s["hit1_answerable_kept"] == round(1 / 3, 4) and s["hit1_answerable_run"] == 1.0
    assert s["stage2"] == {"called": 5, "skipped_by_classifier": 2, "errors": 1}, s["stage2"]
    assert s["latency_ms"]["classifier_refused"] == {"n": 3, "p50": 120.0, "p95": 552.0}, s["latency_ms"]
    c = s["classifier"]
    assert c["theta"] == -1.0 and c["n_scored"] == 7 and c["refused"] == 3
    assert c["top_score"]["unanswerable"] == {"n": 4, "min": -4.0, "median": -1.25, "max": 1.0}, c["top_score"]
    assert s["checks"] == {"classifier_refused_but_stage2_called": 1, "gate_refuse_without_refuser": 1,
                           "no_gate_span": 1, "rows_without_answerable_span": 1}, s["checks"]
    assert s["false_refusals"] == [{"qid": "c2", "qtype": "single", "refuser": "classifier", "top_score": -2.0},
                                   {"qid": "o1", "qtype": "single", "refuser": "other", "top_score": 3.0}]
    assert s["missed"] == [{"qid": "m1", "reason": "no_item", "top_score": 0.5}]
    assert s["has_gate"] and s["has_classifier"]
    md = render_md("selftest", ["- retriever `graph:x`"], s, "P 0.500 · R 0.400 · F1 0.444 (tp 2, fp 2, fn 3)")
    for key in ("# 실제 거절 내역 (gate) — selftest", "## 1. 최종 판정", "| 답 없음을 거절(맞음) | 4/5 |",
                "| 답 있음을 거절(오거절) | 2/3 |", "| 분류기(classifier) | 2 | 1 |", "| 기타(other) | 0 | 1 |",
                "| no_period | 2/2 | 2/2 | 0/2 | 0/2 |", "## 6. 분류기 점수", "| c2 | single | 분류기(classifier) | -2.000 |",
                "| m1 | no_item | 0.500 |", "참고: τ 규칙(파이프라인에는 없음) P 0.500", "classifier_refused_but_stage2_called: 1"):
        assert key in md, key
    assert md.endswith("\n") and not md.endswith("\n\n") and "질문" not in md
    line = summary_line(s)
    assert line.startswith("- 답 없음 (gate, 그래프의 실제 거절): P 0.667 · R 0.800") and "\n" not in line, line
    assert "분류기 3(오거절 1), 2차 LLM 1(오거절 0), 후보 없음 1(오거절 0), 기타 1(오거절 1)" in line, line
    # empty input and rows without any spans: no gate, no classifier, nothing raises
    e = summarize([])
    assert e["final"]["f1"] == 0.0 and e["hit1_answerable_kept"] is None and not e["has_gate"]
    bare = summarize([row_of({"qid": "x", "keys": ["A"], "answerable": True}), row_of({"qid": "y", "keys": []})])
    assert not bare["has_gate"] and not bare["has_classifier"] and bare["final"]["false_refusals"] == 1
    for t in (e, bare):
        out = render_md("empty", [], t)
        assert "분류기가 꺼져 있었음" in out and "## 6." not in out and "- 없음" in out
        assert "분류기 0, 2차 LLM 0, 후보 없음" in summary_line(t)
    print("selftest ok")


if __name__ == "__main__":
    selftest()
