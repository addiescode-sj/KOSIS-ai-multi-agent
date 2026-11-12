"""T3-20: every layer renders report.html (agent conversations + span waterfall, l012 parts, capped generic tables)."""

import json
from argparse import Namespace

from evals import ops, report_html
from evals.tasks.agent import run as run_agent
from tests.test_evals_agent import CHAT, scenario

BASE = {"stage": "t", "split": "all", "source_snapshot": {"tables.jsonl": "0" * 64}, "git": "0" * 40, "git_dirty": False,
        "retriever": None, "jaeger": None}


def write_run(tmp_path, layer: str, rows: list[dict], summary: dict, report: str, op: dict | None = None, **extra):
    run = tmp_path / layer
    run.mkdir()
    (run / "results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    (run / "report.md").write_text(report, encoding="utf-8")
    manifest = {**BASE, "run_id": f"run-{layer}", "layer": layer, "golden": {"path": "g", "sha256": "1" * 64},
                "summary": summary, "ops": op if op is not None else ops.summarize(rows), **extra}
    (run / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return run


def test_agent_page_shows_conversations_and_span_waterfall(tmp_path):
    path = tmp_path / "s.jsonl"
    chat = scenario(*[(t.question, t.expect.model_dump()) for t in CHAT.turns[1:]], sid="s2")  # no calc_qid: no Parquet
    path.write_text(chat.model_dump_json() + "\n", encoding="utf-8")
    rows, summary, report = run_agent(Namespace(scenarios=path, split="all", calc_golden=[], agent=None, repeat=1, stage="t",
                                                oracle=True, from_run=None))
    rows[0]["errors"], rows[0]["passed"] = ["lost_state"], False
    rows[0]["trace_id"] = "a" * 32
    rows[0]["spans"] = [{"name": "invoke_agent", "kind": "turn", "start_ms": 0.0, "duration_ms": 40.0, "depth": 0, "status": "ok"},
                        {"name": "execute_tool make_chart", "kind": "tool", "start_ms": 5.0, "duration_ms": 20.0, "depth": 1, "status": "error"}]
    run = write_run(tmp_path, "agent", rows, summary, report, agent="oracle",
                    jaeger={"ui": "http://localhost:16686", "service": "kosis-agent-evals"})
    page = report_html.render(run)
    assert "대화 성공률" in page and "읽는 법" in page and "대화별 결과와 trace" in page
    assert "execute_tool make_chart" in page and "tool-k err" in page  # waterfall bar, error outline
    assert f'http://localhost:16686/trace/{"a" * 32}' in page and "✕ 실패" in page
    assert "span 없음" in page  # turns without spans (oracle) say so


def test_l012_page_has_one_section_per_part_with_its_ops(tmp_path):
    rows = [{"key": "101_A", "measure": "출생아", "latency_ms": 0.0, "layer": "l0"},
            {"qid": "q1", "split": "dev", "regions": 0, "all_fields": 0, "latency_ms": 0.2, "layer": "l2"}]
    summary = {"l0": {"n_tables": 1, "n_gold": 1}, "l2": {"metrics": {"all": {"n": 1, "all_fields": 0.0, "regions": 0.0}}}}
    report = ("<!-- l0 -->\n# L0 metadata quality — t\n\n| 필드 | 채움률 |\n| --- | --- |\n| measure | 1.000 |\n\n"
              "<!-- l2 -->\n# L2 query understanding — t\n\n- 빈 슬롯도 채점\n\n## 운영 지표 — l0\n\n- Latency: 0")
    op = {name: ops.summarize([r for r in rows if r["layer"] == name]) for name in summary}
    run = write_run(tmp_path, "l012", rows, summary, report, op=op,
                    golden={"l0·l1": {"path": "a", "sha256": "2" * 64}, "l2": {"path": "b", "sha256": "3" * 64}})
    page = report_html.render(run)
    assert "L0 메타데이터" in page and "L2 질의 이해" in page and page.count('<h2 style="margin-top:28px">운영 지표</h2>') == 2
    assert "<td>measure</td>" in page and "모든 필드 정답" in page and "골든 l2 333333333333" in page
    assert "Latency: 0" not in page  # report.md's own ops text is replaced by the ops section


def test_generic_table_puts_problem_rows_first_and_caps(tmp_path):
    rows = [{"qid": f"q{i}", "split": "dev", "all_fields": int(i != 250), "latency_ms": 0.0} for i in range(300)]
    run = write_run(tmp_path, "l2", rows, {"metrics": {"all": {"n": 300, "all_fields": 299 / 300}}}, "# L2 — t\n")
    page = report_html.render(run)
    assert "300건, 주의 1건 먼저" in page and f"{report_html.ROW_CAP}행까지 표시" in page
    assert page.index("<td>q250</td>") < page.index("<td>q0</td>")
    assert "모든 행의 지연이 0 ms" in page


def test_l3_page_still_renders(tmp_path):
    rows = [{"qid": "c1", "kind": "answerable", "verdict": "PASS", "errors": [], "latency_ms": 10.0}]
    summary = {"verdicts": {"all": {"PASS": 1}, "answerable": {"PASS": 1}}, "errors": {}}
    page = report_html.render(write_run(tmp_path, "l3", rows, summary, "# L3 — t\n"))
    assert "L3 계산" in page and "kind별 판정" in page


def test_l3_e2e_report_shows_the_live_trace(tmp_path):
    """T3-32: report.html carries each question's trace (span summary + waterfall), problem rows first."""
    import json

    from evals import report_html

    spans = [{"name": "invoke_agent", "kind": "turn", "start_ms": 0, "duration_ms": 50, "depth": 0, "status": "ok"},
             {"name": "execute_tool summarize_stat", "kind": "tool", "start_ms": 5, "duration_ms": 40, "depth": 1, "status": "ok"},
             {"name": "analyze.llm_plan", "kind": "llm", "start_ms": 6, "duration_ms": 30, "depth": 2, "status": "error"}]
    rows = [{"qid": q, "kind": "answerable", "verdict": v, "errors": [] if v == "PASS" else ["wrong_value"], "spans": [],
             "failure": None, "latency_ms": 50.0, "llm_calls": 1, "trace_id": "ab" * 16, "trace_spans": spans}
            for q, v in (("c1", "PASS"), ("c2", "FAIL"))]
    html = report_html.traces_body({"jaeger": {"ui": "http://j"}}, rows)
    assert "시간이 어디서 쓰였나" in html and "analyze.llm_plan" in html and "invoke_agent</td>" not in html  # root skipped
    assert html.index("<b>c2</b>") < html.index("<b>c1</b>")  # the FAIL row first
    assert 'class="bar llm-k err"' in html and "http://j/trace/" + "ab" * 16 in html
    assert "trace가 없다" in report_html.traces_body({}, [{**rows[0], "trace_spans": []}])
