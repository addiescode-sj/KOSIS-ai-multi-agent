"""e2e (L4) and l3 since T3-32 (option C): each golden question is one turn of the agent graph, live-traced, graded
like L3. Offline: the graph is build_graph over a fake search module and a fake phase 1 call."""

import json

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from kosis_agent.graph.main_graph import build_graph
from kosis_agent.graph.state import new_checkpointer
from kosis_ai.schemas import Refusal, StatAnswer
from kosis_chat.schemas.io import StatOutput
from kosis_rag.schemas.io import SearchOutput

from evals.runners import agent as runner
from evals.runners import e2e
from evals.runners.multiturn_dev import candidate
from evals.schemas import CalcItem, RetrievalItem
from tests.test_agent_graph import STAT_LLM

STEPS = [{"name": "judge", "start_ms": 0.0, "duration_ms": 1.0, "status": "ok", "attributes": {}},
         {"name": "analyze.llm_plan", "start_ms": 1.0, "duration_ms": 5.0, "status": "ok",
          "attributes": {"gen_ai.request.model": "HCX-005", "gen_ai.usage.input_tokens": 10,
                         "gen_ai.usage.output_tokens": 2}}]
RERANK2 = {"node": "rerank2", "seconds": 0.1, "stage2": "clova", "stage2_calls": 2, "stage2_input_tokens": 900,
           "stage2_output_tokens": 40, "stage2_seconds": 0.1}


class Search:
    """A SearchModule stand-in: candidates in rank order (picked = the first), or a gate refusal."""

    def __init__(self, keys, route="found", spans=()):
        self.keys, self.route, self.spans = keys, route, list(spans)

    def stream(self, inp):
        tables = [candidate(k) for k in self.keys]
        if self.route == "refuse":
            yield "output", SearchOutput(question=inp.question, route="refuse", refusal_reason="no_item",
                                         refusal_text="찾는 항목이 있는 통계표가 없어요.", candidates=tables, spans=self.spans)
        else:
            yield "output", SearchOutput(question=inp.question, route="found", picked=tables[:1], candidates=tables,
                                         spans=self.spans)


def refused(question, table_id, **kw):
    used.append(table_id)
    text = "이 표로는 답할 수 없어요."
    return StatOutput(answer=StatAnswer(outcome="refused", text=text, refusal=Refusal(reason="out_of_scope", message=text)),
                      chart=None, spans=STEPS)


used: list[str] = []


def graph(keys, route="found", summarize=refused, spans=()):
    return build_graph(Search(keys, route, spans), STAT_LLM, summarize=summarize, checkpointer=new_checkpointer())


def item() -> CalcItem:
    return CalcItem(qid="c1", kind="not_answerable", question="2030년 출생아 수", table_key="101_DT_GOLD",
                    table_sha256="0", allowed_reasons=["out_of_scope", "no_item"])


def rows(g, n=1):
    return runner.single_turns(g, [(f"c{i + 1}", item().question) for i in range(n)], runner.tracing("e2e"))


def test_the_table_phase1_answered_from_is_used_and_located():
    used.clear()
    [row] = rows(graph(["101_DT_OTHER", "101_DT_GOLD"]))
    assert used == ["101_DT_OTHER"] and row["used_table"] == "101_DT_OTHER" and not row["failed"]
    assert row["answer"].outcome == "refused" and row["spans"] == STEPS and row["trace_id"] and row["span_id"]
    loc = e2e.locate(item(), row, "PASS")
    assert loc["gold_rank"] == 2 and not loc["right_table"] and loc["cause"] == "pass"
    assert e2e.locate(item(), row, "FAIL")["cause"] == "retrieval"
    [right] = rows(graph(["101_DT_GOLD"]))
    assert e2e.locate(item(), right, "FAIL") | {"top_keys": None} == {
        "used_table": "101_DT_GOLD", "right_table": True, "right_alternate": False, "l1_linked": False, "gold_rank": 1,
        "top_keys": None, "gold_keys": ["101_DT_GOLD"], "cause": "answer"}
    # §6.3: with the L1 item of the same qid, an alternate key of its slot is the right table too
    l1 = RetrievalItem(qid="c1", split="test", qtype="sibling", source="v2", question="q",
                       required=[["101_DT_GOLD", "101_DT_OTHER"]])
    alt = e2e.locate(item(), row, "FAIL", l1)
    assert alt["right_table"] and alt["right_alternate"] and alt["l1_linked"] and alt["cause"] == "answer" and alt["gold_rank"] == 1


def test_a_gate_refusal_is_graded_as_a_refusal_and_a_crash_as_a_run_failure():
    [row] = rows(graph(["101_DT_OTHER"], route="refuse"))
    assert row["error"] is None and row["answer"].outcome == "refused" and row["answer"].refusal.reason == "no_item"
    assert row["spans"] == [] and row["used_table"] == "101_DT_OTHER"  # phase 1 never ran

    def broken(question, table_id, **kw):
        raise RuntimeError("the model call failed (format_error)")

    [row] = rows(graph(["101_DT_GOLD"], summarize=broken))
    assert row["answer"] is None and row["failed"] and row["error"].startswith("RuntimeError")


def test_ops_come_from_the_chat_spans_like_the_agent_layer():
    """T3-31/32: rerank2 (one logical call, HTTP attempts only retry) + phase 1 LLM steps, read off the live spans."""
    [row] = rows(graph(["101_DT_GOLD"], spans=[RERANK2, {"node": "gate", "seconds": 0.01}]))
    assert (row["llm_calls"], row["input_tokens"], row["output_tokens"], row["retried"]) == (2, 910, 42, True)
    [plain] = rows(graph(["101_DT_GOLD"]))
    assert (plain["llm_calls"], plain["input_tokens"], plain["retried"]) == (1, 10, False)


def build_fake():
    """`--agent tests.test_evals_e2e:build_fake` for the run_eval tests."""
    return graph(["101_DT_OTHER", "101_DT_GOLD"])


def test_run_eval_e2e_writes_report_and_links_verdicts(monkeypatch, tmp_path):
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from evals import ops, report_html, run_eval
    from evals.evaluators import calculation

    golden = tmp_path / "calculation_golden.jsonl"
    golden.write_text(item().model_dump_json() + "\n", encoding="utf-8")
    monkeypatch.setattr(run_eval, "CALC_GOLDEN", golden)
    monkeypatch.setattr(run_eval, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(run_eval, "ROOT", tmp_path)  # golden path in the manifest is relative to ROOT
    history: list[str] = []
    monkeypatch.setattr(ops, "append_history", lambda run_id, *a, **kw: history.append(run_id))
    monkeypatch.setattr(report_html, "read_history", lambda: [])
    monkeypatch.setattr(calculation, "read_cells", lambda path: {})
    memory = InMemorySpanExporter()
    tracing = runner.tracing

    def traced(layer="agent"):
        provider, mine = tracing(layer)
        provider.add_span_processor(SimpleSpanProcessor(memory))
        return provider, mine

    monkeypatch.setattr(runner, "tracing", traced)
    out = run_eval.main(["--layer", "e2e", "--agent", "tests.test_evals_e2e:build_fake", "--stage", "t"])
    assert [out] == list((tmp_path / "reports").iterdir()) and len(history) == 1  # main returns the run folder
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "검색 1위가 정답 표: 0/1" in report and "retrieval 0" in report  # refusal with an allowed reason passes
    assert "## 실패 단계 (trace)" in report and "실패 없음" in report
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "L4 종단 간" in html and "실패 단계 (trace)" in html
    # one live trace per question under service kosis-agent / environment eval, the verdict linked afterwards
    spans = memory.get_finished_spans()
    root = next(s for s in spans if s.name == "invoke_agent")
    assert (root.resource.attributes["service.name"], root.resource.attributes["deployment.environment.name"],
            root.resource.attributes["kosis.eval.layer"]) == ("kosis-agent", "eval", "e2e")
    assert {"execute_tool search_tables", "execute_tool summarize_stat", "analyze.llm_plan"} <= {s.name for s in spans}
    verdict = next(s for s in spans if s.name == "eval.question")
    assert verdict.links[0].context.trace_id == root.context.trace_id and verdict.attributes["kosis.verdict"] == "PASS"

    # T3-21 --llm mock: the path runs, the run says it is mock, and its numbers stay out of ops_history
    mock = run_eval.main(["--layer", "e2e", "--agent", "tests.test_evals_e2e:build_fake", "--stage", "t-mock",
                          "--llm", "mock"])
    assert json.loads((mock / "manifest.json").read_text(encoding="utf-8"))["llm"] == "mock"
    assert "llm mock: 경로 확인용, 점수 아님" in (mock / "report.md").read_text(encoding="utf-8") and len(history) == 1


def test_golden_sources_prefer_split_files(tmp_path):
    from evals.builders.phase1 import sources

    assert sources(tmp_path) == []
    (tmp_path / "golden.jsonl").write_text("", encoding="utf-8")
    assert sources(tmp_path) == [tmp_path / "golden.jsonl"]  # merged file when the split files are absent
    for name in ("stat_golden_edge.jsonl", "stat_golden.jsonl"):
        (tmp_path / name).write_text("", encoding="utf-8")
    assert [f.name for f in sources(tmp_path)] == ["stat_golden.jsonl", "stat_golden_edge.jsonl"]
    assert sources(tmp_path / "golden.jsonl") == [tmp_path / "golden.jsonl"]


class Reranked(BaseRetriever):
    """A retriever whose stage 2 ran: two HTTP attempts, usage on the top document (as phase 2 rerank2 records it)."""

    meta: dict

    def _get_relevant_documents(self, query: str, *, run_manager) -> list[Document]:
        return [Document(page_content="", metadata={"key": "101_DT_GOLD", **self.meta})]


def test_l1_counts_rerank2_like_the_agent_layer_and_traces_live():
    """T3-31: one logical call per rerank2 run; T3-32: each L1 search is a live `execute_tool search_tables` trace."""
    from evals.runners import retrieval

    meta = {"stage2": "clova", "stage2_calls": 2, "stage2_input_tokens": 900, "stage2_output_tokens": 40}
    telemetry = runner.tracing("l1")
    [l1] = retrieval.run(Reranked(meta=meta), [RetrievalItem(qid="r1", split="test", qtype="exact_name", source="v2",
                                                             question="q", required=[["101_DT_GOLD"]])], 10, telemetry)
    assert (l1["llm_calls"], l1["input_tokens"], l1["output_tokens"], l1["retried"]) == (1, 900, 40, True)
    assert l1["trace_id"] and l1["span_id"]


def test_mock_turns_every_model_off_before_the_graph_is_built(monkeypatch):
    """--llm mock must not call a model anywhere: the service .env may say STAGE2=hcx (T3-32)."""
    import os
    from types import SimpleNamespace

    from evals.tasks.common import agent_graph

    for name, value in (("STAT_PROVIDER", "hcx"), ("STAGE2", "hcx"), ("INTERPRETER", "hybrid")):
        monkeypatch.setenv(name, value)
    seen = agent_graph(SimpleNamespace(llm="mock", agent=None),
                       lambda: {k: os.environ[k] for k in ("STAT_PROVIDER", "STAGE2", "INTERPRETER")})
    assert seen == {"STAT_PROVIDER": "mock", "STAGE2": "none", "INTERPRETER": "rules"}
