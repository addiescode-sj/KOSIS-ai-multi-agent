"""Online evaluation (plans/T3-43.md): a service turn rebuilt from its Jaeger trace, code checks over the conversation,
drift against earlier online records, failure cases kept apart from refusals. Offline: fake Jaeger JSON, fake judge."""

import datetime as dt
import json

from evals.runners import online as source
from evals.tasks import online

STAT = {"outcome": "answered", "text": "2024년 서울 출생아는 100명입니다.\n\n출처: KOSIS / 조사 / 표",
        "result": {"facts": [{"id": "f1", "value": 100.0, "source_ref": "DT_X|T1|11|2024", "unit": "명", "period": "2024"}]},
        "parts": [{"kind": "text", "text": "2024년 서울 출생아는 "}, {"kind": "fact", "fact_id": "f1"}, {"kind": "text", "text": "입니다."}],
        "source": {"org_id": "101", "tbl_id": "DT_X", "survey_nm": "조사", "tbl_nm": "표"}}


def tag(k, v):
    return {"key": k, "type": "string" if isinstance(v, str) else "int64", "value": v}


def jaeger_trace(question="2024년 서울 출생아 수", answer=STAT["text"], trace="ab" * 16, turn=0, conv="c1", error=False):
    root = {"traceID": trace, "spanID": "01" * 8, "operationName": "invoke_agent", "references": [], "startTime": 1_000_000,
            "duration": 2_000_000, "tags": [tag("gen_ai.conversation.id", conv), tag("kosis.turn.index", turn),
                                             tag("kosis.outcome", "answered"),
                                             *([tag("kosis.turn.question", question)] if question else []),
                                             tag("kosis.turn.answer", answer), tag("kosis.turn.table_key", "101_DT_X"),
                                             *([tag("otel.status_code", "ERROR")] if error else [])]}
    tool = {"traceID": trace, "spanID": "02" * 8, "operationName": "execute_tool summarize_stat", "startTime": 1_500_000,
            "duration": 1_000_000, "references": [{"refType": "CHILD_OF", "spanID": "01" * 8}],
            "tags": [tag("gen_ai.tool.name", "summarize_stat"), tag("gen_ai.tool.call.arguments", json.dumps({"table_key": "101_DT_X"})),
                     tag("gen_ai.tool.call.result", json.dumps(STAT, ensure_ascii=False))]}
    return {"traceID": trace, "spans": [root, tool]}


def test_a_service_trace_rebuilds_the_turn():
    t = source.turn_of_trace(jaeger_trace())
    assert t["question"] == "2024년 서울 출생아 수" and t["answer"] == STAT["text"] and t["outcome"] == "answered"
    assert [c["name"] for c in t["calls"]] == ["summarize_stat"] and t["calls"][0]["result"]["outcome"] == "answered"
    assert t["trace_id"] == "ab" * 16 and t["conversation"] == "c1" and t["turn"] == 0 and t["table_key"] == "101_DT_X"
    assert source.turn_of_trace(jaeger_trace(question=None)) is None  # a trace written before T3-43: not readable
    assert source.turn_of_trace(jaeger_trace(error=True))["outcome"] == "error"


def test_code_checks_ground_numbers_in_the_conversation():
    first = source.turn_of_trace(jaeger_trace())
    made_up = {**source.turn_of_trace(jaeger_trace(trace="cd" * 16, turn=1)), "calls": [],
               "answer": "작년보다 37명 늘었습니다."}  # 37 is in no question, fact or sum of the conversation
    again = {**made_up, "answer": "앞에서 본 100명입니다."}  # 100 is a fact of the earlier turn
    checks = online.code_checks([first, made_up, again])
    assert checks[0] == {"unsupported_numbers": [], "missing_sources": []}
    assert checks[1]["unsupported_numbers"] == ["37"] and checks[2]["unsupported_numbers"] == []
    no_source = {**first, "answer": "100명입니다.", "conversation": "c2"}
    assert online.code_checks([no_source])[0]["missing_sources"] == ["101_DT_X"]


def record(**metrics):
    return {"key": {"judge": "j"}, "n": 100, "n_judged": 40, "stderr": {"faithfulness": 0.02},
            "metrics": {"faithfulness": 0.85, "error_rate": 0.05, **metrics}}


def test_drift_needs_history_and_uses_the_sample_error():
    past = [record(), record(faithfulness=0.86), record(faithfulness=0.84)]
    assert online.drift(record(), past[:2])["status"] == "insufficient"
    assert online.drift(record(faithfulness=0.82), past)["status"] == "ok"  # 0.03 below the median: within max(0.03, 2*0.02)
    low = online.drift(record(faithfulness=0.70), past)
    assert low["status"] == "degraded" and low["metrics"]["faithfulness"]["degraded"]
    assert online.drift(record(error_rate=0.20), past)["metrics"]["error_rate"]["degraded"]  # lower is better for rates
    other = [{**r, "key": {"judge": "other"}} for r in past]
    assert online.drift(record(faithfulness=0.1), other)["status"] == "insufficient"  # another judge never compares


def test_failure_cases_keep_refusals_apart_dedupe_and_expire(tmp_path):
    now = dt.datetime(2026, 11, 17, 12, 0)
    rows = [{"qid": "a", "trace_id": "a", "question": "q", "answer": "", "outcome": "error", "unsupported_numbers": [],
             "missing_sources": []},
            {"qid": "b", "trace_id": "b", "question": "q", "answer": "", "outcome": "refused", "no_answer_reason": "no_period",
             "unsupported_numbers": [], "missing_sources": []},
            {"qid": "c", "trace_id": "c", "question": "q", "answer": "x", "outcome": "answered", "unsupported_numbers": [],
             "missing_sources": [], "faithfulness": 0.9}]
    cases = online.failure_cases(rows, now)
    assert [c["kinds"] for c in cases] == [["error"], ["refusal_review:no_period"]]  # a good answer is no case
    path = tmp_path / "failures.jsonl"
    old = {**cases[0], "id": "old", "collected": (now - dt.timedelta(days=31)).isoformat()}
    path.write_text(json.dumps(old) + "\n", encoding="utf-8")
    assert online.collect(cases, now, path) == 2
    assert online.collect(cases, now, path) == 0  # the same traces again: nothing new
    kept = [json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines()]
    assert kept == ["a", "b"]  # the 31-day-old case expired


def test_layer_online_from_a_past_run(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rows = [{"sid": "s1", "turn": 0, "question": "2024년 서울 출생아 수", "answer": STAT["text"], "error": None,
             "calls": [{"name": "summarize_stat", "args": {"table_key": "101_DT_X"}, "result": STAT, "status": "ok"}],
             "trace_id": "ab" * 16, "span_id": "01" * 8, "latency_ms": 10.0},
            {"sid": "s2", "turn": 0, "question": "오늘 날씨", "answer": "", "error": "boom", "calls": [], "trace_id": "cd" * 16}]
    (run_dir / "results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(online, "HISTORY", tmp_path / "history.jsonl")
    monkeypatch.setattr(online, "FAILURES", tmp_path / "failures.jsonl")
    monkeypatch.setattr(online.offline, "make_scorer", lambda args: (lambda jobs: [(0.9, None, 1.0) for _ in jobs], "0.4.3"))

    class Args:
        from_run, stage, sample, seed, no_record, no_judge_cache = run_dir, "t", 5, 0, False, False
    out, summary, report = online.run(Args)
    assert summary["n"] == 2 and summary["n_judged"] == 1 and summary["metrics"]["faithfulness"] == 0.9
    assert summary["outcomes"] == {"answered": 1, "error": 1} and summary["failure_cases"] == {"error": 1}
    assert summary["drift"]["status"] == "insufficient" and "온라인 평가" in report
    online.record_history("r1", summary)
    assert json.loads((tmp_path / "history.jsonl").read_text())["run_id"] == "r1"
    assert online.span_attributes(out[0])["kosis.online.faithfulness"] == 0.9
