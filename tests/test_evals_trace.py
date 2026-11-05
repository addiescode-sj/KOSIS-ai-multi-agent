"""evals.trace: one blame rule per failure stage, the rule order, and the OTLP replay (in-memory exporter)."""

import pytest

from evals import trace


def span(name, status="ok", **attributes):
    return {"name": name, "start_ms": 0.0, "duration_ms": 1.0, "status": status, "attributes": attributes}


RULES = [  # (row fields, spans, expected stage, expected span)
    ({"right_table": False, "used_table": "T_OTHER", "gold_rank": None}, [span("judge")], "retrieval", "retrieve"),
    ({"errors": ["run_failure"], "error": "RuntimeError: x"}, [span("judge"), span("compose", "error")], "tool_call", "compose"),
    ({"errors": ["wrong_value"]}, [span("judge"), span("analyze.rules", "error"), span("fallback_chatbot")], "tool_skipped", "fallback_chatbot"),
    ({"errors": ["false_refusal"]}, [span("judge"), span("analyze.rules"), span("compute", outcome="refused", reason="no_period")], "compute", "compute"),
    ({"errors": ["false_refusal"]}, [span("judge", outcome="refused", final=True)], "understanding", "judge"),
    ({"errors": ["wrong_unit"]}, [span("judge"), span("analyze.rules"), span("analyze.llm_fill"), span("compute")], "understanding", "analyze.llm_fill"),
    ({"errors": ["unsupported_claim"]}, [span("judge"), span("analyze.rules"), span("compute"), span("compose")], "compose", "compose"),
    ({"errors": ["something_new"]}, [span("judge")], "unknown", None),
]


@pytest.mark.parametrize("fields, spans, stage, where", RULES, ids=[r[2] + "-" + str(i) for i, r in enumerate(RULES)])
def test_each_rule(fields, spans, stage, where):
    row = {"qid": "q", "kind": "answerable", "verdict": "FAIL", "errors": [], "spans": spans, **fields}
    f = trace.blame(row)
    assert (f["stage"], f["span"]) == (stage, where) and f["label"] == trace.STAGES[stage]


def test_pass_is_not_blamed_and_order_wins():
    assert trace.blame({"verdict": "PASS", "errors": [], "kind": "answerable"}) is None
    # several errors: the first rule decides, the rest stay in the reason
    row = {"qid": "q", "kind": "answerable", "verdict": "FAIL", "errors": ["unsupported_claim", "wrong_dims"],
           "spans": [span("judge"), span("analyze.rules"), span("compute"), span("compose")]}
    f = trace.blame(row)
    assert f["stage"] == "understanding" and "unsupported_claim" in f["reason"]
    # a refusal the compute module made is the compute stage, not understanding
    row = {**row, "errors": ["false_refusal"], "spans": [span("judge"), span("compute", outcome="refused")]}
    assert trace.blame(row)["stage"] == "compute"
    # verify errors before a good retry are normal flow: an answered row is never tool_call
    row = {**row, "errors": ["wrong_value"], "spans": [span("judge"), span("analyze.rules"), span("compute"), span("verify", "error")]}
    assert trace.blame(row)["stage"] == "understanding"


def test_export_replays_one_trace_per_question(monkeypatch):
    from opentelemetry.exporter.otlp.proto.grpc import trace_exporter
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    assert trace.export([], "l3", None) is False  # no endpoint, nothing sent
    memory = InMemorySpanExporter()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", lambda: memory)
    row = {"qid": "q", "kind": "answerable", "verdict": "FAIL", "errors": ["wrong_unit"], "latency_ms": 10.0,
           "right_table": True, "retrieve_ms": 2.0, "used_table": "T", "gold_rank": 1, "top_keys": ["T"],
           "spans": [span("judge"), span("analyze.rules", detail={"a": 1}), span("compute")]}
    row["failure"] = trace.blame(row)
    assert trace.export([row], "e2e", "bm25") is True
    spans = {s.name: s for s in memory.get_finished_spans()}
    assert set(spans) == {"eval.question", "retrieve", "tool.summarize_stat", "judge", "analyze.rules", "compute"}
    assert {s.context.trace_id for s in spans.values()} == {int(row["trace_id"], 16)}
    assert spans["analyze.rules"].parent.span_id == spans["tool.summarize_stat"].context.span_id
    assert not spans["analyze.rules"].status.is_ok and spans["eval.question"].attributes["failure.stage"] == "understanding"


# --- T2-36: LangGraph node spans ---

def node(name, keys=None, **attributes):
    sp = span(name, **attributes)
    return sp if keys is None else {**sp, "keys": keys}


LOST = [  # (node outputs, gold, k, expected node, words in the reason)
    ([node("parse"), node("retrieve", ["A", "B", "G"]), node("rerank1", ["A", "B", "G"]), node("gate")], {"G"}, 2, "retrieve", "3위"),
    ([node("retrieve", ["A", "B"]), node("rerank1", ["B", "A"])], {"G"}, 1, "retrieve", "풀(2개)에 없음"),
    ([node("retrieve", ["G", "A"]), node("rerank1", ["G", "A"]), node("rerank2", ["A", "G"])], {"G"}, 1, "rerank2", "1위 → 2위"),
    # raised by rerank1, pushed out again by rerank2: the last drop counts
    ([node("retrieve", ["A", "B", "G"]), node("rerank1", ["G", "A", "B"]), node("rerank2", ["A", "B", "G"])], {"G"}, 1, "rerank2", "1위 → 3위"),
    # dropped by rerank1, raised back by rerank2 but still out: rerank1
    ([node("retrieve", ["G", "A", "B"]), node("rerank1", ["A", "B", "G"]), node("rerank2", ["A", "G", "B"])], {"G"}, 1, "rerank1", "1위 → 3위"),
    # several gold keys: the best one counts
    ([node("retrieve", ["A", "H", "G"]), node("rerank1", ["A", "B", "H"])], {"G", "H"}, 2, "rerank1", "2위 → 3위"),
]


@pytest.mark.parametrize("spans, gold, k, where, words", LOST)
def test_lost_at_names_the_node(spans, gold, k, where, words):
    name, reason = trace.lost_at(spans, gold, k)
    assert name == where and words in reason


def test_lost_at_without_node_spans():
    assert trace.lost_at([], {"G"}, 1) is None
    assert trace.lost_at([span("judge")], {"G"}, 1) is None  # Phase 1 spans have no keys


def test_blame_search_for_l1_rows():
    nodes = [node("retrieve", ["G", "A"]), node("rerank1", ["A", "G"]), node("gate", route="guide")]
    row = {"answerable": True, "keys": ["A", "G"], "no_answer_pred": False, "search_spans": nodes}
    f = trace.blame_search(row, {"G"}, 1)
    assert (f["stage"], f["span"]) == ("retrieval", "rerank1")
    assert trace.blame_search(row, {"G"}, 2) is None  # a hit within k
    # a plain retriever: no nodes, the search as a whole
    assert trace.blame_search({**row, "search_spans": []}, {"G"}, 1)["span"] == "retrieve"
    # no-answer calls: refused an answerable one / guided an unanswerable one -> gate
    f = trace.blame_search({**row, "no_answer_pred": True}, {"G"}, 2)
    assert (f["stage"], f["span"], f["label"]) == ("no_answer", "gate", trace.NO_ANSWER_LABEL) and "route=guide" in f["reason"]
    assert trace.blame_search({**row, "answerable": False}, set(), 2)["span"] == "gate"
    assert trace.blame_search({**row, "answerable": False, "no_answer_pred": True}, set(), 2) is None


def test_e2e_retrieval_failure_names_the_graph_node():
    row = {"qid": "q", "kind": "answerable", "verdict": "FAIL", "errors": ["wrong_value"], "right_table": False,
           "used_table": "A", "gold_rank": 2, "gold_keys": ["G"], "spans": [span("judge")],
           "search_spans": [node("retrieve", ["G", "A"]), node("rerank2", ["A", "G"])]}
    f = trace.blame(row)
    assert (f["stage"], f["span"]) == ("retrieval", "rerank2") and "1위 → 2위" in f["reason"]
    assert trace.blame({**row, "search_spans": []})["span"] == "retrieve"  # a plain retriever, as before


def test_export_nests_graph_nodes_under_search_tables(monkeypatch):
    from opentelemetry.exporter.otlp.proto.grpc import trace_exporter
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporters = []  # export() shuts its provider down, and the exporter with it: a fresh one per call
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", lambda: exporters.append(InMemorySpanExporter()) or exporters[-1])
    nodes = [node("parse"), node("retrieve", ["G", "A"]), node("rerank2", ["A", "G"]), node("gate", route="guide")]
    l1 = {"qid": "q", "qtype": "concept", "answerable": True, "keys": ["A", "G"], "rank": 2, "no_answer_pred": False,
          "latency_ms": 5.0, "search_spans": nodes}
    l1["failure"] = trace.blame_search(l1, {"G"}, 1)
    assert trace.export([l1], "l1", "graph:staged") is True
    spans = {s.name: s for s in exporters[-1].get_finished_spans()}
    assert set(spans) == {"eval.question", "search_tables", "parse", "retrieve", "rerank2", "gate"}  # no tool in L1
    assert all(spans[n].parent.span_id == spans["search_tables"].context.span_id for n in ("parse", "retrieve", "rerank2", "gate"))
    assert not spans["rerank2"].status.is_ok and spans["retrieve"].status.is_ok
    assert spans["eval.question"].attributes["failure.span"] == "rerank2"

    # e2e: search_tables next to tool.summarize_stat
    e2e = {"qid": "q", "kind": "answerable", "verdict": "FAIL", "errors": [], "latency_ms": 10.0, "retrieve_ms": 4.0,
           "right_table": False, "used_table": "A", "gold_rank": 2, "gold_keys": ["G"], "top_keys": ["A", "G"],
           "spans": [span("judge")], "search_spans": nodes}
    e2e["failure"] = trace.blame(e2e)
    assert trace.export([e2e], "e2e", "graph:staged") is True
    spans = {s.name: s for s in exporters[-1].get_finished_spans()}
    assert set(spans) == {"eval.question", "search_tables", "tool.summarize_stat", "judge", "parse", "retrieve", "rerank2", "gate"}
    assert spans["judge"].parent.span_id == spans["tool.summarize_stat"].context.span_id


def test_every_grader_error_has_info():
    import re
    from pathlib import Path

    from evals.evaluators import calculation

    added = set(re.findall(r'errors\.add\("(\w+)"', Path(calculation.__file__).read_text(encoding="utf-8")))
    assert added | set().union(*calculation.FAIL.values()) <= calculation.ERROR_INFO.keys()
