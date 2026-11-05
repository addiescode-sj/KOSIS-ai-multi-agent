"""e2e (first part of L4): the retriever's top 1 table goes to summarize_stat, graded like L3; offline with stubs."""

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from kosis_ai.schemas import Refusal, StatAnswer, StatResult

from evals.runners import e2e
from evals.schemas import CalcItem, QueryItem, RetrievalItem


class Fixed(BaseRetriever):
    keys: list[str]

    def _get_relevant_documents(self, query: str, *, run_manager) -> list[Document]:
        return [Document(page_content="", metadata={"key": k}) for k in self.keys]


class Client:
    model = "stub"

    def reset(self) -> None:
        self.calls, self.input_tokens, self.output_tokens, self.failures = 1, 10, 2, 0


def refused(reason: str) -> StatAnswer:
    return StatAnswer.model_construct(outcome="refused", result=StatResult(), parts=[], clarify_options=[],
                                      default_applied={}, refusal=Refusal(reason=reason, message="x"))


def stub_pipeline(monkeypatch, used: list[str]) -> None:
    def summarize(question, table_key, client, retry_interval=0.0):
        used.append(table_key)
        return refused("no_period"), [{"name": "judge", "start_ms": 0, "duration_ms": 1, "status": "ok", "attributes": {}}], None

    monkeypatch.setattr("evals.trace.summarize", summarize)
    monkeypatch.setattr("evals.ops.usage_client", lambda: Client())


def item() -> CalcItem:
    return CalcItem(qid="c1", kind="not_answerable", question="q", table_key="101_DT_GOLD", table_sha256="0",
                    allowed_reasons=["no_period"])


def test_top1_table_is_used_and_located(monkeypatch):
    used: list[str] = []
    stub_pipeline(monkeypatch, used)
    [row] = e2e.run(Fixed(keys=["101_DT_OTHER", "101_DT_GOLD"]), [item()], k=10)
    assert used == ["101_DT_OTHER"] and row["used_table"] == "101_DT_OTHER" and not row["failed"]
    assert row["input_tokens"] == 10 and row["model"] == "stub" and row["spans"][0]["name"] == "judge"
    loc = e2e.locate(item(), row, "PASS")
    assert loc["gold_rank"] == 2 and not loc["right_table"] and loc["cause"] == "pass"
    assert e2e.locate(item(), row, "FAIL")["cause"] == "retrieval"
    right = e2e.run(Fixed(keys=["101_DT_GOLD"]), [item()], k=10)[0]
    assert e2e.locate(item(), right, "FAIL") | {"top_keys": None} == {
        "used_table": "101_DT_GOLD", "right_table": True, "right_alternate": False, "l1_linked": False, "gold_rank": 1,
        "top_keys": None, "gold_keys": ["101_DT_GOLD"], "cause": "answer"}
    # §6.3: with the L1 item of the same qid, an alternate key of its slot is the right table too
    l1 = RetrievalItem(qid="c1", split="test", qtype="sibling", source="v2", question="q",
                       required=[["101_DT_GOLD", "101_DT_OTHER"]])
    alt = e2e.locate(item(), row, "FAIL", l1)
    assert alt["right_table"] and alt["right_alternate"] and alt["l1_linked"] and alt["cause"] == "answer" and alt["gold_rank"] == 1


def test_empty_retrieval_is_a_run_failure(monkeypatch):
    used: list[str] = []
    stub_pipeline(monkeypatch, used)
    [row] = e2e.run(Fixed(keys=[]), [item()], k=10)
    assert used == [] and row["failed"] and row["error"] == "no table retrieved" and row["used_table"] is None
    assert e2e.locate(item(), row, "FAIL")["gold_rank"] is None


def test_run_eval_e2e_writes_report(monkeypatch, tmp_path):
    from evals import ops, report_html, run_eval
    from evals.evaluators import calculation
    from evals.runners import retrieval

    stub_pipeline(monkeypatch, [])
    golden = tmp_path / "calculation_golden.jsonl"
    golden.write_text(item().model_dump_json() + "\n", encoding="utf-8")
    monkeypatch.setattr(run_eval, "CALC_GOLDEN", golden)
    monkeypatch.setattr(run_eval, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(run_eval, "ROOT", tmp_path)  # golden path in the manifest is relative to ROOT
    monkeypatch.setattr(ops, "append_history", lambda *a, **kw: None)
    monkeypatch.setattr(report_html, "read_history", lambda: [])
    monkeypatch.setattr(calculation, "read_cells", lambda path: {})
    monkeypatch.setattr(retrieval, "build", lambda name, items, k: Fixed(keys=["101_DT_OTHER", "101_DT_GOLD"]))
    assert run_eval.main(["--layer", "e2e", "--retriever", "bm25-record", "--stage", "t"]) == 0
    [out] = (tmp_path / "reports").iterdir()
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "검색 1위가 정답 표: 0/1" in report and "retrieval 0" in report  # refusal with an allowed reason passes
    assert "## 실패 단계 (trace)" in report and "실패 없음" in report
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "L4 종단 간" in html and "실패 단계 (trace)" in html


def test_golden_sources_prefer_split_files(tmp_path):
    from evals.builders.phase1 import sources

    assert sources(tmp_path) == []
    (tmp_path / "golden.jsonl").write_text("", encoding="utf-8")
    assert sources(tmp_path) == [tmp_path / "golden.jsonl"]  # merged file when the split files are absent
    for name in ("stat_golden_edge.jsonl", "stat_golden.jsonl"):
        (tmp_path / name).write_text("", encoding="utf-8")
    assert [f.name for f in sources(tmp_path)] == ["stat_golden.jsonl", "stat_golden_edge.jsonl"]
    assert sources(tmp_path / "golden.jsonl") == [tmp_path / "golden.jsonl"]
