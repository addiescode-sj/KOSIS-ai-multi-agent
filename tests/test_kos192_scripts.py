"""KOS-192 offline scripts (scripts/kos192/): the pure aggregation and metric functions, the split refusals (report
--items, trigger_analysis, gen_rewrites, rw_eval), gen_rewrites' resume rule and the L1 runner's stage 1 signals.
Plan 07: the --preset presets, the rewrite RRF (kos192_lib.fuse_rewrite), recall_compare's R1 from another build, and
rw_eval's path choice, fused stage 1 order, top 5 protection, resume rule and summary / paired comparison (per
split); the rewrite prompt choice (--rewrite-prompt) and its cache key; trigger_select's tau choice, selection rule,
bootstrap and test criteria. No model, no search, no server: rows, documents and golden items are built here.
"""

import importlib.util
import sys
import json
import math
from pathlib import Path

import pytest

from evals.schemas import RetrievalItem

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "scripts" / "kos192"


def _script(name: str):
    """scripts/kos192/<name>.py as a module (each script puts its own folder on sys.path for kos192_lib)."""
    spec = importlib.util.spec_from_file_location(f"kos192_{name}", FOLDER / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name while the class is built
    spec.loader.exec_module(module)
    return module


lib = _script("kos192_lib")


def _item(qid, split="dev", qtype="concept", required=(("K",),), answerable=True, question="비밀 질문"):
    if not answerable:
        return RetrievalItem(qid=qid, split=split, qtype="no_answer", source="v2", question=question,
                             answerable=False, no_answer_reason="out_of_scope")
    return RetrievalItem(qid=qid, split=split, qtype=qtype, source="v2", question=question,
                         required=[list(s) for s in required])


# --- kos192_lib -----------------------------------------------------------------------------------------------------

def test_auc_is_rank_based_with_ties_counted_half():
    assert lib.auc_low([1, 2], [3, 4]) == 1.0  # every positive (miss) scores below every negative (hit1)
    assert lib.auc_low([3, 4], [1, 2]) == 0.0
    assert lib.auc_low([1, 1], [1, 1]) == 0.5
    assert lib.auc_low([1, 2], [2, 3]) == pytest.approx(3.5 / 4)  # the (2, 2) pair counts 1/2
    assert lib.auc_low([], [1]) is None and lib.auc_low([1], []) is None


def test_trigger_threshold_triggers_the_rate():
    scores = [i / 20 for i in range(1, 21)]
    for rate, k in ((0.20, 4), (0.35, 7), (0.50, 10)):
        tau = lib.trigger_threshold(scores, rate)
        assert sum(lib.triggered(s, tau) for s in scores) == k
        assert scores[k - 1] < tau < scores[k]  # the midpoint between the k-th and (k+1)-th score
    assert lib.trigger_threshold(scores, 0.0) == scores[0] and lib.trigger_threshold(scores, 1.0) == math.inf
    assert lib.trigger_threshold([], 0.2) is None


def test_tied_scores_trigger_fewer_never_more():
    tied = [0.1, 0.5, 0.5, 0.5, 0.9]
    tau = lib.trigger_threshold(tied, 0.4)
    assert tau == 0.5 and sum(lib.triggered(s, tau) for s in tied) == 1
    assert not lib.triggered(None, 0.5) and not lib.triggered(0.1, None)


def test_quantile_interpolates():
    assert lib.quantile([4, 1, 3, 2], 0.5) == 2.5 and lib.quantile([5], 0.9) == 5 and lib.quantile([], 0.5) is None


def test_bigram_overlap_is_the_share_of_the_name_found_in_the_question():
    assert lib.char_bigrams("인구 이동") == {"인구", "이동"}  # no bigram across the space
    assert lib.name_overlap("시도별 전입자 수", "전입자 수 - 시도") == 1.0
    assert lib.name_overlap("이사 들어온 사람", "전입자 수 - 시도") == 0.0
    assert lib.best_overlap("전입자 통계", ["이동자 수", "전입자 수"]) == 1.0  # best over the OR-group
    assert lib.best_overlap("q", []) is None


def test_json_never_holds_infinity_and_inputs_fail_in_one_line(tmp_path):
    out = tmp_path / "o.json"
    lib.write_json(out, {"tau": math.inf, "x": [float("nan"), 1.5]})
    assert json.loads(out.read_text(encoding="utf-8")) == {"tau": None, "x": [None, 1.5]}
    with pytest.raises(SystemExit, match="golden file not found"):
        lib.load_golden(tmp_path / "missing.jsonl")
    with pytest.raises(SystemExit, match="results.jsonl"):
        lib.load_run(tmp_path)
    bom = tmp_path / "bom.jsonl"
    bom.write_bytes("\ufeff".encode("utf-8") + b'{"qid": "a"}\n')
    assert lib.read_jsonl(bom) == [{"qid": "a"}] and lib.read_jsonl(tmp_path / "none.jsonl") == []


def test_stage1_signals_of_the_whole_pool():
    from langchain_core.documents import Document

    from evals.runners.retrieval import stage1_signals

    docs = [Document(page_content="", metadata={"key": k, **({"stage1_score": s} if s is not None else {})})
            for k, s in (("A", 0.2), ("B", 0.9), ("C", 0.5), ("D", None))]
    top, margin = stage1_signals(docs)
    assert top == 0.9 and margin == pytest.approx(0.4)  # the score's top, wherever it is in the order
    assert stage1_signals(docs[:1]) == (0.2, None) and stage1_signals(docs[3:]) == (None, None)
    assert stage1_signals([]) == (None, None)


def test_route_no_answer_is_the_gate_rule():
    assert lib.route_no_answer({"keys": []})  # nothing came back (or the search failed)
    assert lib.route_no_answer({"keys": ["A"], "stage2_picked_n": 0, "stage2_error": None})
    assert not lib.route_no_answer({"keys": ["A"], "stage2_picked_n": 0, "stage2_error": "TimeoutError"})
    assert not lib.route_no_answer({"keys": ["A"], "stage2_picked_n": None})  # stage 2 off


# --- report.py ------------------------------------------------------------------------------------------------------

def _report_fixture():
    golden = {i.qid: i for i in (
        _item("d1", qtype="exact_name", required=(("101_DT_SECRET", "101_DT_ALT"),)),
        _item("d2", required=(("101_DT_B",),)),
        _item("d3", answerable=False),
        _item("t1", split="test", required=(("101_DT_C",),)),
        _item("t2", split="test", answerable=False),
    )}
    rows = [
        {"qid": "d1", "split": "dev", "keys": ["101_DT_ALT", "X"], "latency_ms": 100.0},
        {"qid": "d2", "split": "dev", "keys": ["X", "Y", "101_DT_B"], "latency_ms": 200.0},
        {"qid": "d3", "split": "dev", "keys": ["X"], "stage2_picked_n": 0, "stage2_error": None, "latency_ms": 300.0},
        {"qid": "t1", "split": "test", "keys": ["101_DT_C"], "stage2_picked_n": 0, "latency_ms": 400.0},
        {"qid": "t2", "split": "test", "keys": [], "error": "boom", "latency_ms": 500.0},
        {"qid": "x9", "split": "dev", "keys": ["X"], "latency_ms": 1.0},
    ]
    meta = {"d1": {"style": "S0"}, "d2": {"style": "S1"}, "d3": {"style": "S1"}, "t1": {"style": "S0"}}
    return golden, rows, meta


def test_report_groups_and_metrics():
    report = _script("report")
    golden, rows, meta = _report_fixture()
    graded, unknown = report.grade_rows(rows, golden, meta, "all")
    assert unknown == 1 and len(graded) == 5
    groups = report.aggregate(graded)
    assert list(groups) == ["all", "split", "style", "qtype"]  # no rewrite_status in these rows
    a = groups["all"]["all"]
    assert (a["n"], a["n_answerable"], a["n_no_answer"], a["errors"]) == (5, 3, 2, 1)
    assert a["hit@1"] == pytest.approx(2 / 3)  # d1 by its alternative key, t1; d2 is 3rd
    assert a["mrr"] == pytest.approx((1 + 1 / 3 + 1) / 3)
    assert (a["no_answer"]["tp"], a["no_answer"]["fp"], a["no_answer"]["fn"]) == (2, 1, 0)  # t1: picked nothing
    assert a["latency_p50_ms"] == 300.0
    assert groups["split"]["dev"]["hit@1"] == 0.5 and groups["split"]["test"]["n"] == 2
    assert groups["style"]["S0"]["hit@1"] == 1.0 and groups["style"]["S1"]["hit@1"] == 0.0
    assert groups["style"]["-"]["n"] == 1  # t2 has no meta line
    assert set(groups["qtype"]) == {"concept", "exact_name", "no_answer"}
    s0 = groups["style"]["S0"]["no_answer"]  # d1 and t1: nothing to find, so no P/R/F1
    assert (s0["precision"], s0["recall"], s0["f1"], s0["fp"]) == (None, None, None, 1)
    assert groups["style"]["S1"]["no_answer"]["recall"] == 1.0

    dev, _ = report.grade_rows(rows, golden, meta, "dev")
    assert {g["qid"] for g in dev} == {"d1", "d2", "d3"}


def test_report_rewrite_status_group_when_rows_carry_it():
    report = _script("report")
    golden, rows, meta = _report_fixture()
    rows = [{**r, "rewrite_status": "used" if r["qid"] == "d2" else "skipped"} for r in rows]
    groups = report.aggregate(report.grade_rows(rows, golden, meta, "all")[0])
    assert set(groups["rewrite_status"]) == {"used", "skipped"} and groups["rewrite_status"]["used"]["n"] == 1


def test_report_prints_no_question_and_no_key():
    report = _script("report")
    golden, rows, meta = _report_fixture()
    graded, unknown = report.grade_rows(rows, golden, meta, "all")
    summary = {"run_id": "r", "retriever": "graph:staged-gpt", "split": "all", "n_graded": len(graded),
               "not_in_golden": unknown, "groups": report.aggregate(graded)}
    text = report.render(summary) + json.dumps(summary, ensure_ascii=False)
    assert "ΔHit@1 vs S0" in text
    for secret in ("비밀 질문", "101_DT_SECRET", "101_DT_ALT", "101_DT_B", "101_DT_C", "d1", "t2"):
        assert secret not in text


def test_items_only_with_dev(tmp_path):
    report = _script("report")
    for split in ("test", "all"):
        with pytest.raises(SystemExit, match="--items"):
            report.check_items_allowed(split, True)
        with pytest.raises(SystemExit, match="--items"):  # refused before any file is read
            report.main([str(tmp_path), "--split", split, "--items"])
    with pytest.raises(SystemExit, match="--items"):
        report.main([str(tmp_path), "--items"])  # default split: all
    report.check_items_allowed("dev", True)
    report.check_items_allowed("test", False)


def test_items_list_dev_lines_only(tmp_path, capsys):
    report = _script("report")
    golden, rows, meta = _report_fixture()
    run = tmp_path / "run"
    run.mkdir()
    (run / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    gold = tmp_path / "golden.jsonl"
    gold.write_text("".join(i.model_dump_json() + "\n" for i in golden.values()), encoding="utf-8")
    meta_file = tmp_path / "meta.jsonl"
    meta_file.write_text("".join(json.dumps({"qid": q, **m}) + "\n" for q, m in meta.items()), encoding="utf-8")
    assert report.main([str(run), "--golden", str(gold), "--meta", str(meta_file), "--split", "dev", "--items"]) == 0
    out = capsys.readouterr().out
    assert "| d1 | S0 | 1 | 101_DT_ALT |" in out and "| d2 | S1 | 3 | X |" in out and "| d3 | S1 | n/a | X |" in out
    assert "t1" not in out and "101_DT_C" not in out
    graded, _ = report.grade_rows(rows, golden, meta, "all")
    assert "t1" not in report.item_lines(rows, graded)  # a test line is never printed


# --- trigger_analysis.py --------------------------------------------------------------------------------------------

def test_trigger_analysis_labels_auc_and_triggers():
    ta = _script("trigger_analysis")
    golden = {i.qid: i for i in (_item("h1"), _item("h2"), _item("m1"), _item("m2"), _item("n1", answerable=False),
                                 _item("t9", split="test"))}
    rows = [{"qid": "h1", "keys": ["K"], "stage1_top_score": 0.9, "stage1_margin": 0.5, "top_score": 0.8},
            {"qid": "h2", "keys": ["K", "X"], "stage1_top_score": 0.8, "stage1_margin": 0.05, "top_score": 0.3},
            {"qid": "m1", "keys": ["X", "K"], "stage1_top_score": 0.2, "stage1_margin": 0.1, "top_score": 0.7},
            {"qid": "m2", "keys": ["X"], "stage1_top_score": 0.3, "stage1_margin": None, "top_score": None},
            {"qid": "n1", "keys": ["X"], "stage1_top_score": 0.1, "stage1_margin": 0.0, "top_score": 0.2},
            {"qid": "t9", "keys": ["X"], "stage1_top_score": 0.0, "stage1_margin": 0.0, "top_score": 0.0}]
    labeled = ta.label_rows(rows, golden, "dev")
    assert {r["qid"]: r["group"] for r in labeled} == {"h1": "hit1", "h2": "hit1", "m1": "miss", "m2": "miss",
                                                       "n1": "no_answer"}
    s1 = ta.analyze(labeled, "stage1_top_score")
    assert (s1["n"], s1["missing"], s1["counts"]) == (5, 0, {"hit1": 2, "miss": 2, "no_answer": 1})
    assert s1["auc_miss_vs_hit1"] == 1.0 and s1["auc_miss_or_no_answer_vs_hit1"] == 1.0
    assert s1["quantiles"]["hit1"]["0.5"] == pytest.approx(0.85)
    t20, t35, _ = s1["triggers"]
    assert t20["tau"] == pytest.approx(0.15) and (t20["triggered"], t20["miss_caught"], t20["no_answer_triggered"]) == (1, 0, 1)
    assert t35["tau"] == pytest.approx(0.25) and (t35["miss_caught"], t35["miss_caught_share"]) == (1, 0.5)
    assert t35["wasted_share"] == 0.0 and t35["trigger_rate"] == pytest.approx(2 / 5)
    cos = ta.analyze(labeled, "top_score")
    assert (cos["n"], cos["missing"]) == (4, 1)  # m2 has no dense score
    assert cos["auc_miss_vs_hit1"] == 0.5  # m1 0.7: below h1 0.8, above h2 0.3
    margin = ta.analyze(labeled, "stage1_margin")
    assert (margin["n"], margin["missing"]) == (4, 1)
    assert margin["auc_miss_vs_hit1"] == 0.5  # m1 0.1: below h1 0.5, above h2 0.05
    assert list(ta.SCORES) == ["stage1_top_score", "stage1_margin", "top_score"]
    text = ta.render({"stage1_top_score": s1, "stage1_margin": margin, "top_score": cos}, "r", "dev")
    assert "비밀 질문" not in text and "stage1_margin" in text


def test_trigger_analysis_is_dev_only(tmp_path):
    ta = _script("trigger_analysis")
    for split in ("test", "all"):
        with pytest.raises(SystemExit, match="dev split only"):  # refused before any file is read
            ta.main([str(tmp_path), "--split", split])
    ta.check_split("dev")


# --- recall_compare.py ----------------------------------------------------------------------------------------------

def _recall_records():
    s = "R1@w1"
    return [
        {"qid": "r1", "answerable": True, "stage1_top_score": 0.1, "never": 30, "settings": {s: {"status": "used", "rank": 5}}},
        {"qid": "r2", "answerable": True, "stage1_top_score": 0.9, "never": 2, "settings": {s: {"status": "used", "rank": 8}}},
        {"qid": "r3", "answerable": True, "stage1_top_score": 0.5, "never": None,
         "settings": {s: {"status": "none", "rank": None}}},
        {"qid": "n1", "answerable": False, "stage1_top_score": 0.2, "never": None, "settings": {}},
    ]


def test_recall_policies():
    rc = _script("recall_compare")
    assert rc.setting_name("R2", 0.5) == "R2@w0.5" and rc.setting_name("R1", 1.0) == "R1@w1"
    assert rc.better(None, 3) == 3 and rc.better(3, None) == 3 and rc.better(2, 3) == 2
    result = rc.compare(_recall_records(), ["R1@w1"], {"t": 0.3})["R1@w1"]
    p = result["policies"]
    assert p["never"]["n"] == 3  # answerable only
    assert (p["never"]["recall@10"], p["never"]["recall@50"]) == pytest.approx((1 / 3, 2 / 3))
    assert p["never"]["mrr"] == pytest.approx((1 / 30 + 1 / 2) / 3)
    assert p["always"]["recall@10"] == pytest.approx(2 / 3) and p["always"]["mrr"] == pytest.approx((1 / 5 + 1 / 8) / 3)
    gated = p["gated@t"]  # only r1 (0.1 < 0.3) takes the rewrite pool
    assert gated["mrr"] == pytest.approx((1 / 5 + 1 / 2) / 3) and gated["trigger_rate"] == pytest.approx(1 / 3)
    assert gated["tau"] == 0.3
    assert p["oracle"]["mrr"] == pytest.approx((1 / 5 + 1 / 2) / 3)
    assert (result["oracle_beats_never"], result["always_worse"]) == (1, 1)  # r1 better, r2 worse
    # r1 goes from 30 to 5: into the top 20 and the top 10 under always and oracle; nothing falls out
    assert result["moves"]["always"] == {"enter@50": 0, "leave@50": 0, "enter@20": 1, "leave@20": 0,
                                         "enter@10": 1, "leave@10": 0}
    assert result["moves"]["oracle"]["enter@20"] == 1 and result["stop"]  # 1 < 3: the stop rule fires
    text = rc.render({"R1@w1": result}, 4, 3, {"R1": {"used": 2, "none": 1}})
    assert "중단 규칙 발동" in text and "모든 설정에서 발동" in text and "진단" not in text
    assert "| R@1 | R@5 |" in text
    diag = rc.render({"R1@w1": result}, 4, 3, {"R1": {"used": 2, "none": 1}}, "staged-condyears")
    assert "진단:" in diag and "중단 규칙 발동" not in diag and "멈추고 보고" not in diag  # plan 07 Q2: diagnosis only


def test_moves_count_entering_and_falling_out():
    rc = _script("recall_compare")
    m = rc.moves([2, None, 15], [12, 5, None])
    assert (m["enter@10"], m["leave@10"]) == (1, 1)  # 5 enters, 2 -> 12 falls out
    assert (m["enter@20"], m["leave@20"]) == (1, 1)  # 5 enters, 15 -> None falls out
    assert (m["enter@50"], m["leave@50"]) == (1, 1)


def test_recall_taus_use_every_dev_item_and_a_missing_setting_keeps_never():
    rc = _script("recall_compare")
    taus = rc.taus_for(_recall_records())  # scores 0.1, 0.2, 0.5, 0.9 (the no-answer item included)
    assert list(taus) == ["20%", "35%", "50%"]
    assert taus["20%"] == pytest.approx(0.15) and taus["50%"] == pytest.approx(0.35)
    record = {"never": 4, "stage1_top_score": 0.1, "settings": {}}
    assert rc.policy_rank(record, "R2@w2", "always") == 4
    assert rc.recall_metrics([1, 15, None, 60]) == {"n": 4, "recall@1": 0.25, "recall@5": 0.25, "recall@10": 0.25,
                                                    "recall@20": 0.5, "recall@50": 0.5,
                                                    "mrr": pytest.approx((1 + 1 / 15 + 1 / 60) / 4)}
    with pytest.raises(ValueError):
        rc.policy_rank(record, "R1@w1", "sometimes")


# --- gen_rewrites.py ------------------------------------------------------------------------------------------------

def test_gen_rewrites_test_split_needs_allow_test():
    gr = _script("gen_rewrites")
    for split in ("test", "all"):
        with pytest.raises(SystemExit, match="allow-test"):  # before settings, models or files
            gr.main(["--split", split])
    with pytest.raises(SystemExit, match="--split must be one of"):
        gr.check_split("train", allow_test=True)
    gr.check_split("dev")
    gr.check_split("test", allow_test=True)
    gr.check_split("all", allow_test=True)
    assert gr.splits_of("all") == ("dev", "test") and gr.splits_of("dev") == ("dev",)


def test_rewrite_prompt_reaches_rewrite_question(monkeypatch):
    from kosis_rag.rewrite import node
    from kosis_rag.rewrite.constants import PROMPT_FILE, PROMPT_FILE_V2
    from kosis_rag.rewrite.schemas import RewriteOutput

    gr = _script("gen_rewrites")
    asked = []

    def fake(inp, client, *, prompt):
        asked.append(prompt)
        return RewriteOutput(status="none")

    monkeypatch.setattr(node, "rewrite_question", fake)
    assert gr.rewrite_checked("q", [], None, None, None, prompt=PROMPT_FILE_V2).status == "none"
    gr.rewrite_checked("q", [], None, None, None)  # no prompt: rewrite_question's default, v1
    assert asked == [PROMPT_FILE_V2, PROMPT_FILE]
    assert lib.rewrite_prompt_file("v1") == PROMPT_FILE and lib.rewrite_prompt_file("v2") == PROMPT_FILE_V2
    assert gr.prompt_sha(node.system_prompt(PROMPT_FILE)) != gr.prompt_sha(node.system_prompt(PROMPT_FILE_V2))
    assert lib.REWRITE_BUILDS["v2"] != lib.REWRITE_BUILDS["v1"]  # each prompt's rows in their own build


def test_rw_eval_inline_rewriter_passes_the_prompt(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import gen_rewrites  # the module rw_eval.make_generator imports (scripts/kos192 is on sys.path)
    from kosis_rag.rewrite.schemas import RewriteOutput

    asked = []

    def fake(question, names, client, parsed, resources, *, prompt=None):
        asked.append(prompt)
        return RewriteOutput(rewrite="전입자 수", status="used", seconds=1.5)

    monkeypatch.setattr(gen_rewrites, "rewrite_checked", fake)
    cache = tmp_path / "rewrites.jsonl"
    search = {"collection": "c", "embed_ckpt": "e.pt", "rerank_ckpt": "r.pt"}
    generate = rw.make_generator(None, sha="s2", model="gemma", preset="staged-condyears", variant="R1",
                                 resources=None, cache=cache, prompt="rewrite-v2.md", search=search)
    row = generate(_item("a"), SimpleNamespace(docs=[], query=None))
    assert asked == ["rewrite-v2.md"] and row["search"] == search
    assert (row["prompt"], row["prompt_sha"], row["model"], row["status"]) == ("rewrite-v2.md", "s2", "gemma", "used")
    assert row["wall_seconds"] >= 0 and json.loads(cache.read_text(encoding="utf-8")) == row  # cached at once


def test_rw_eval_cache_of_another_prompt_is_not_reused():
    gr = _script("gen_rewrites")
    v1, v2 = gr.prompt_sha("prompt v1"), gr.prompt_sha("prompt v2")
    rows = [{"qid": "a", "variant": "R2", "status": "used", "prompt_sha": v1, "model": "g", "preset": "p"},
            {"qid": "b", "variant": "R2", "status": "used", "prompt_sha": v2, "model": "g", "preset": "p"},
            {"qid": "c", "variant": "R2", "status": "error", "prompt_sha": v2, "model": "g", "preset": "p"},
            {"qid": "d", "variant": "R1", "status": "used", "prompt_sha": v2, "model": "g", "preset": "p"},
            {"qid": "e", "variant": "R2", "status": "used", "prompt_sha": v2, "model": "other", "preset": "p"},
            {"qid": "f", "variant": "R2", "status": "used"}]  # made before prompt_sha was recorded
    assert list(rw.cached_rewrites(rows, sha=v2, model="g", preset="p", variant="R2")) == ["b"]
    assert list(rw.cached_rewrites(rows, sha=v1, model="g", preset="p", variant="R2")) == ["a"]
    assert list(rw.source_rewrites(rows, sha=v2, variant="R2")) == ["b", "c", "e"]  # --rewrites-from: any model
    assert rw.source_rewrites(rows, sha=gr.prompt_sha("prompt v3"), variant="R2") == {}


def test_rw_eval_cache_matches_the_search():
    from types import SimpleNamespace

    gr = _script("gen_rewrites")
    sha = gr.prompt_sha("prompt v2")
    search = {"collection": "kosis_mixft_E2s_e2", "embed_ckpt": "e2s-e2.pt", "rerank_ckpt": "rr-e2s-e2.pt"}

    def row(qid, s=None):
        r = {"qid": qid, "variant": "R2", "status": "used", "prompt_sha": sha, "model": "g", "preset": "p"}
        return {**r, "search": s} if s is not None else r

    rows = [row("a", search), row("b", {**search, "collection": "other"}), row("c"),  # c: cached before "search"
            row("d", {"collection": "kosis_mixft_E2s_e2"}), row("e", {**search, "embed_ckpt": "e1.pt"})]
    kw = {"sha": sha, "model": "g", "preset": "p", "variant": "R2"}
    assert list(rw.cached_rewrites(rows, search=search, **kw)) == ["a", "c", "d"]  # a missing field matches
    assert list(rw.cached_rewrites(rows, **kw)) == ["a", "b", "c", "d", "e"]
    spec = SimpleNamespace(chroma=SimpleNamespace(collection="kosis_mixft_E2s_e2"),
                           embed_ckpt="data\\models\\embedder\\E2s-e2.pt", rerank_ckpt="D:/repo/data/models/reranker/RR-E2s-e2.pt")
    assert lib.rewrite_search(SimpleNamespace(spec=spec)) == search
    assert lib.same_search(None, search) and lib.same_search(search, None) and not lib.same_search(rows[1]["search"], search)
    item = _item("a")
    from kosis_rag.rewrite.schemas import RewriteOutput

    out = RewriteOutput(status="none")
    made = gr.rewrite_row(item, "R2", out, top=None, margin=None, sha=sha, model="g", preset="p", search=search)
    assert made["search"] == search and "search" not in gr.rewrite_row(item, "R2", out, top=None, margin=None, sha=sha,
                                                                        model="g", preset="p")
    assert gr.default_build("v1") == "v1" and gr.default_build("v2") == "v3-condyears-p2"


def test_read_jsonl_skips_a_torn_last_line(tmp_path, capsys):
    path = tmp_path / "results.jsonl"
    path.write_text('{"qid": "a"}\n{"qid": "b"}\n{"qid": "c", "ke', encoding="utf-8")  # a write cut off
    assert [r["qid"] for r in lib.read_jsonl(path)] == ["a", "b"]
    assert "cut off" in capsys.readouterr().err
    path.write_text('{"qid": "a"}\n{"qid": \n{"qid": "c"}\n', encoding="utf-8")  # a bad line before the last
    with pytest.raises(json.JSONDecodeError):
        lib.read_jsonl(path)
    path.write_text("", encoding="utf-8")
    assert lib.read_jsonl(path) == [] and lib.read_jsonl(tmp_path / "none.jsonl") == []


def test_resume_skips_only_finished_rows_of_this_prompt_and_model():
    gr = _script("gen_rewrites")
    sha, old = gr.prompt_sha("prompt v1"), gr.prompt_sha("prompt v0")
    rows = [
        {"qid": "a", "variant": "R1", "status": "used", "prompt_sha": sha, "model": "gemma"},
        {"qid": "a", "variant": "R2", "status": "error", "prompt_sha": sha, "model": "gemma"},  # asked again
        {"qid": "b", "variant": "R1", "status": "used", "prompt_sha": old, "model": "gemma"},  # another prompt
        {"qid": "c", "variant": "R1", "status": "none", "prompt_sha": sha, "model": "other"},  # another model
        {"qid": "d", "variant": "R1", "status": "used"},  # made before prompt_sha / model were recorded
        {"qid": "e", "variant": "R1", "status": "error", "prompt_sha": sha, "model": "gemma"},
        {"qid": "e", "variant": "R1", "status": "rejected", "prompt_sha": sha, "model": "gemma"},  # the retry
    ]
    assert gr.done_pairs(rows, sha, "gemma") == {("a", "R1"), ("e", "R1")}
    current = gr.current_rows(rows, sha, "gemma")
    assert [(r["qid"], r["variant"], r["status"]) for r in current] == [("a", "R1", "used"), ("a", "R2", "error"),
                                                                         ("e", "R1", "rejected")]
    assert len(sha) == 64 and sha != old

def test_rewrite_summary_by_variant_and_c5():
    gr = _script("gen_rewrites")
    items = {i.qid: i for i in (_item("a1", required=(("K1", "K2"),), question="이사 들어온 사람 수"),
                                _item("a2", required=(("K3",),), question="2020년 서울 인구"),
                                _item("n1", answerable=False, question="아파트 값"))}
    names_of = {"K1": "전입자 수", "K2": "이동자 수", "K3": "주민등록인구"}
    lines = [
        {"qid": "a1", "variant": "R1", "rewrite": "전입자 수", "status": "used", "reason": None, "seconds": 1.0},
        {"qid": "a2", "variant": "R1", "rewrite": "2020년 서울 인구", "status": "rejected", "reason": "unchanged",
         "seconds": 2.0},
        {"qid": "n1", "variant": "R1", "rewrite": None, "status": "none", "reason": None, "seconds": 3.0},
        {"qid": "a1", "variant": "R2", "rewrite": None, "status": "error", "reason": "TimeoutError: t", "seconds": 0.5},
        {"qid": "zz", "variant": "R1", "rewrite": "x", "status": "used", "reason": None, "seconds": 9.0},  # not dev
    ]
    s = gr.summarize(lines, items, names_of)
    r1, r2 = s["R1"], s["R2"]
    assert r1["n"] == 3 and r1["status"]["all"] == {"used": 1, "none": 1, "rejected": 1, "error": 0}
    assert r1["status"]["answerable"]["rejected"] == 1 and r1["status"]["no_answer"]["none"] == 1
    assert r1["rejected_by_reason"] == {"unchanged": 1}
    assert (r1["given"], r1["identical"]) == (2, 1)
    assert r1["latency_p50_s"] == 2.0
    assert r1["c5"] == {"n": 1, "before_mean": 0.0, "after_mean": 1.0, "went_down": 0}
    assert r2["n"] == 1 and r2["status"]["all"]["error"] == 1 and r2["c5"]["n"] == 0
    text = gr.render(s)
    assert "이사 들어온" not in text and "전입자 수" not in text and "K1" not in text


def test_retrieval_aggregate_skips_empty_values():
    """The P0 crash: stage1_top_score / stage1_margin / top_score are None with 0 or 1 candidates."""
    from evals.evaluators.retrieval import aggregate

    clean = [{"qid": "a", "qtype": "concept", "hit@1": 1, "rr": 1.0, "top_score": 0.8, "rank": 1, "ok": True},
             {"qid": "b", "qtype": "concept", "hit@1": 0, "rr": 0.5, "top_score": 0.4, "rank": 2, "ok": False},
             {"qid": "c", "qtype": "compare", "hit@1": 0, "rr": 0.0, "top_score": 0.2, "rank": None, "ok": False}]
    def old_aggregate(rows):  # the expression before the fix, for the no-None case
        groups = {"all": rows, **{q: [r for r in rows if r["qtype"] == q] for q in dict.fromkeys(r["qtype"] for r in rows)}}
        return {name: {"n": len(ms), **{k: sum(m[k] for m in ms if k in m) / len(ms) for k, v in ms[0].items()
                                        if isinstance(v, (int, float)) and k != "rank" and not isinstance(v, bool)}}
                for name, ms in groups.items()}

    assert aggregate(clean) == old_aggregate(clean)  # no None: exactly the old numbers
    assert aggregate(clean)["concept"] == pytest.approx({"n": 2, "hit@1": 0.5, "rr": 0.75, "top_score": 0.6})
    holes = [dict(clean[0], stage1_margin=0.3), dict(clean[1], top_score=None, stage1_margin=None), clean[2]]
    out = aggregate(holes)  # used to raise TypeError: float + NoneType
    assert out["all"] == pytest.approx({"n": 3, "hit@1": 1 / 3, "rr": 0.5, "top_score": 1.0 / 3, "stage1_margin": 0.1})
    assert out["concept"] == pytest.approx({"n": 2, "hit@1": 0.5, "rr": 0.75, "top_score": 0.4, "stage1_margin": 0.15})
    before = old_aggregate(clean)
    assert all(out[g][k] == before[g][k] for g in before for k in ("n", "hit@1", "rr"))  # metric columns unchanged


# --- presets (plan 07 Q0) -------------------------------------------------------------------------------------------

def test_presets_are_l1_presets_and_the_default_reproduces_v1():
    from evals.runners.retrieval import STAGED

    assert lib.DEFAULT_PRESET == "staged" and lib.PRESETS[0] == "staged"
    assert set(lib.PRESETS) <= set(STAGED) and set(lib.STAGE2_PRESETS) == set(lib.PRESETS)
    for preset, with_llm in lib.STAGE2_PRESETS.items():  # each preset is its stage 2 preset with stage 2 off
        assert STAGED[preset] == {**STAGED[with_llm], "stage2": "none"}
    assert STAGED["staged-condyears"]["cond_scope"] == "years" and lib.STAGE2_PRESETS["staged-condyears"] == "staged-gpt-condyears"


def test_preset_argument_and_row_preset():
    import argparse

    parser = argparse.ArgumentParser()
    lib.add_preset_arg(parser)
    assert parser.parse_args([]).preset == "staged"  # the old scripts' search: v1 reproduces
    assert parser.parse_args(["--preset", "staged-condyears"]).preset == "staged-condyears"
    with pytest.raises(SystemExit):
        parser.parse_args(["--preset", "staged-gpt"])  # an LLM preset is not a stage 1 preset
    other = argparse.ArgumentParser()
    lib.add_preset_arg(other, default="staged-condyears")
    assert other.parse_args([]).preset == "staged-condyears"
    assert lib.row_preset({"qid": "a"}) == "staged" and lib.row_preset({"preset": "staged-nocond"}) == "staged-nocond"
    with pytest.raises(SystemExit, match="unknown preset"):
        lib.stage1_resources("staged-gpt")  # refused before anything is loaded
    with pytest.raises(SystemExit, match="unknown preset"):
        lib.stage2_resources("staged-gpt")


def test_without_stage2_keeps_everything_but_the_selector():
    from kosis_rag.schemas.resources import IndexSpec, SearchOptions, SearchResources

    score, select = (lambda q, keys: [0.0] * len(keys)), (lambda q, keys: keys)
    res = SearchResources(options=SearchOptions(stage2="gpt"), spec=IndexSpec(), records={}, dense="D", bm25="B",
                          text_format="t", stage1_score=score, select=select, stage2_name="gpt")
    off = lib.without_stage2(res)
    assert off.select is None and off.stage2_llm is None and res.select is select
    assert off.stage1_score is score and off.dense == "D" and off.options is res.options


def _docs(keys, scores=None):
    from langchain_core.documents import Document

    return [Document(page_content=k, metadata={"key": k, **({"score": scores[i]} if scores else {})})
            for i, k in enumerate(keys)]


def test_fuse_rewrite_weights_the_rewrite_lists():
    from types import SimpleNamespace

    res = SimpleNamespace(dense="dense", bm25="bm25", hq=None, hq_bm25=None, options=SimpleNamespace(pool=50, hq_weight=1.0))
    runs = {"dense": _docs("AB", [0.9, 0.8]), "bm25": _docs("AC")}
    again = [_docs("D", [0.7]), _docs("D")]
    fused = lib.fuse_rewrite(runs, again, 2.0, res)
    assert [d.metadata["key"] for d in fused] == ["D", "A", "B", "C"]  # D: 2 x 2/(60+1) beats A: 2/(60+1)
    assert fused[0].metadata["rank_dense_rw"] == 1 and fused[0].metadata["rank_bm25_rw"] == 1
    assert fused[0].metadata["score"] == 0.8  # not in pass 1's dense list: the dense floor, as the graph's fuse
    assert [d.metadata["key"] for d in lib.fuse_rewrite(runs, again, 0.5, res)] == ["A", "D", "B", "C"]
    assert [d.metadata["key"] for d in lib.fuse_rewrite(runs, [], 2.0, res)] == ["A", "B", "C"]  # pass 1 alone


# --- recall_compare.py (plan 07 Q2) ---------------------------------------------------------------------------------

def test_recall_compare_reads_r1_from_another_build(tmp_path):
    rc = _script("recall_compare")
    v2, v1 = tmp_path / "v2-condyears", tmp_path / "v1"
    v2.mkdir()
    v1.mkdir()
    (v2 / "rewrites.jsonl").write_text("\n".join(json.dumps(r) for r in (
        {"qid": "a", "variant": "R1", "status": "used", "rewrite": "v2 r1"},
        {"qid": "a", "variant": "R2", "status": "used", "rewrite": "v2 r2", "preset": "staged-condyears"},
        {"qid": "b", "variant": "R2", "status": "none", "rewrite": None})) + "\n", encoding="utf-8")
    (v1 / "rewrites.jsonl").write_text(json.dumps({"qid": "a", "variant": "R1", "status": "rejected", "rewrite": "v1 r1"})
                                       + "\n", encoding="utf-8")
    both = rc.load_rewrites(v2)
    assert both[("a", "R1")]["rewrite"] == "v2 r1" and both[("a", "R2")]["rewrite"] == "v2 r2"
    mixed = rc.load_rewrites(v2, v1)
    assert mixed[("a", "R1")]["rewrite"] == "v1 r1" and mixed[("a", "R2")]["rewrite"] == "v2 r2" and len(mixed) == 3
    assert rc.preset_mismatch(mixed, "staged-condyears") == 1  # b's R2 row has no preset: "staged"
    assert rc.preset_mismatch(mixed, "staged") == 1 and rc.preset_mismatch(mixed, "staged", "R1") == 0


# --- gen_rewrites.py (plan 07 Q1) -----------------------------------------------------------------------------------

def test_gen_rewrites_resume_and_summary_per_preset_and_variant():
    gr = _script("gen_rewrites")
    sha = gr.prompt_sha("p")
    rows = [
        {"qid": "a", "variant": "R2", "status": "used", "prompt_sha": sha, "model": "g"},  # before --preset: staged
        {"qid": "a", "variant": "R2", "status": "used", "prompt_sha": sha, "model": "g", "preset": "staged-condyears"},
        {"qid": "b", "variant": "R2", "status": "error", "prompt_sha": sha, "model": "g", "preset": "staged-condyears"},
    ]
    assert gr.done_pairs(rows, sha, "g") == {("a", "R2")} and len(gr.current_rows(rows, sha, "g")) == 1
    assert gr.done_pairs(rows, sha, "g", "staged-condyears") == {("a", "R2")}
    assert [r["qid"] for r in gr.current_rows(rows, sha, "g", "staged-condyears")] == ["a", "b"]
    assert gr.parse_variants("R2") == ("R2",) and gr.parse_variants("R2, R1") == ("R1", "R2")
    for bad in ("R3", "", " , "):
        with pytest.raises(SystemExit, match="--variants"):
            gr.parse_variants(bad)
    summary = gr.summarize([], {"a": _item("a")}, {}, ("R2",))
    assert list(summary) == ["R2"] and summary["R2"]["n"] == 0


# --- rw_eval.py (plan 07 Q3) ----------------------------------------------------------------------------------------

rw = _script("rw_eval")


def test_rw_eval_split_refusals():
    for argv in (["--split", "test"], ["--split", "all"]):
        with pytest.raises(SystemExit, match="allow-test"):  # before settings, models or files
            rw.main(argv)
    with pytest.raises(SystemExit, match="--split must be one of"):
        rw.check_split("train", allow_test=True)
    rw.check_split("dev")
    rw.check_split("test", allow_test=True)  # the one final run
    rw.check_split("all", allow_test=True)
    assert rw.splits_of("all") == ("dev", "test", "all") and rw.splits_of("test") == ("test",)


def test_rw_eval_path_choice():
    assert rw.choose_path({"status": "used", "rewrite": "전입자 수"}) == ("B", None)
    assert rw.choose_path({"status": "used", "rewrite": " "}) == ("A-fallback", "empty")
    for status in ("none", "rejected", "error"):
        assert rw.choose_path({"status": status, "rewrite": "x"}) == ("A-fallback", status)
    assert rw.choose_path(None) == ("A-fallback", "missing")


def test_fused_stage1_order_mirrors_the_stage1_blend():
    from kosis_rag.rerank.stage1 import blend_order

    assert rw.score_ranks([0.1, 0.9, 0.5, 0.9]) == [4, 1, 3, 2]  # ties rank in input order
    q, r = [0.1, 0.9, 0.5], [0.2, 0.8, 0.9]
    # fused: pos 0 = 2/63, pos 1 = 1/61 + 1/62 = pos 2 (a tie kept in input order)
    assert rw.fused_stage1_order(q, r, "score", c=60) == [1, 2, 0]
    # blend with the input order: pos 1 = 1/61 + 1/62, pos 0 = 1/63 + 1/61, pos 2 = 1/62 + 1/63
    assert rw.fused_stage1_order(q, r, "blend", c=60) == [1, 0, 2]
    s = [0.3, 0.1, 0.9, 0.5, 0.7]
    assert rw.fused_stage1_order(s, s) == blend_order(s)  # one question scored twice = stage 1's own order
    with pytest.raises(ValueError):
        rw.fused_stage1_order(q, r, "max")
    with pytest.raises(ValueError):
        rw.fused_stage1_order(q, r[:2])


def test_rerank1_fused_scores_both_questions_and_keeps_the_tail():
    from types import SimpleNamespace

    table = {"원 질문": {"A": 0.1, "B": 0.9, "C": 0.5}, "재작성": {"A": 0.2, "B": 0.8, "C": 0.9}}
    asked = []

    def score(question, keys):
        asked.append(question)
        return [table[question][k] for k in keys]

    res = SimpleNamespace(stage1_score=score, options=SimpleNamespace(rerank_n=3, rerank_mode="blend"))
    out = rw.rerank1_fused("원 질문", "재작성", _docs("ABCD"), res)
    assert [d.metadata["key"] for d in out] == ["B", "A", "C", "D"] and asked == ["원 질문", "재작성"]
    b = out[0].metadata
    assert (b["input_rank"], b["stage1_score"], b["stage1_rank"], b["stage1_score_rw"], b["stage1_rank_rw"]) == (
        2, 0.9, 1, 0.8, 2)
    assert "stage1_score" not in out[3].metadata  # the tail as given
    assert rw.rerank1_fused("q", "r", [], res) == []


def test_protection_keeps_pass1_top_in_the_stage2_input():
    order = list("ABCDEFGH")
    assert rw.protect_keys(order, ["A", "B"], 3) == (order, [])  # already inside: unchanged
    keys, added = rw.protect_keys(order, ["G", "X"], 4)  # G outside the top 4, X not in the pool at all
    assert keys == ["A", "B", "G", "X", "C", "D", "E", "F", "H"] and added == ["G", "X"]
    keys, added = rw.protect_keys(order, ["A", "H", "B"], 3)  # protected keys are never the ones bumped
    assert keys == ["A", "B", "H", "C", "D", "E", "F", "G"] and added == ["H"]
    assert rw.protect_keys(order, list("ABCDEF"), 3) == (order, [])  # at most k protected
    assert rw.protect_keys(["A"], ["Z"], 20) == (["A", "Z"], ["Z"])  # a short pool: appended
    pass1, ranked = _docs("PQRS"), _docs("ABCDP")
    docs, n = rw.protect_docs(ranked, pass1, 2, 3)
    assert [d.metadata["key"] for d in docs] == ["A", "P", "Q", "B", "C", "D"] and n == 2
    assert docs[1] is ranked[4] and docs[2] is pass1[1]  # the new pool's document when it has the table


def test_rw_eval_rank_fields_latency_and_resume():
    item, none = _item("a", required=(("K",),)), _item("n", answerable=False)
    assert rw.rank_fields(item, ["X", "K"]) == {"rank": 2, "hit@1": 0, "hit@5": 1, "hit@10": 1, "rr": 0.5}
    assert rw.rank_fields(item, ["X"]) == {"rank": None, "hit@1": 0, "hit@5": 0, "hit@10": 0, "rr": 0.0}
    assert set(rw.rank_fields(none, ["K"]).values()) == {None}
    assert rw.total_latency(1000.0, None) == 1000.0 and rw.total_latency(1000.0, 4000.0) == 5000.0
    assert rw.config_id({"x": 1, "y": 2}) == rw.config_id({"y": 2, "x": 1}) != rw.config_id({"x": 1, "y": 3})
    assert rw.config_id({"p": Path("a.pt")}) == rw.config_id({"p": "a.pt"})  # paths hash as text
    used, other = {"status": "used", "rewrite": "전입자 수"}, {"status": "used", "rewrite": "전출자 수"}
    rewrites = {q: used for q in "abcdef"}
    rewrites["f"] = {"status": "error", "rewrite": None}
    h, hf = rw.rewrite_hash(used), rw.rewrite_hash(rewrites["f"])
    assert h != rw.rewrite_hash(other) and rw.rewrite_hash(None) == rw.rewrite_hash({"status": "missing"})

    def row(qid, cid="c1", err=None, rh=h, path="B", reason=None):
        return {"qid": qid, "config_id": cid, "stage2_error": err, "rewrite_hash": rh, "path": path,
                "fallback_reason": reason}

    rows = [row("a"),
            row("b", err="TimeoutError"),
            row("c", cid="c0"),  # another configuration
            row("d", err="HTTP 500"),
            row("b"),  # the retry
            row("e", rh=rw.rewrite_hash(other)),  # made from an older rewrite of e
            row("f", rh=hf, path="A-fallback", reason="error"),  # the rewrite call failed: waits for a rewrite
            row("g", rh=rw.rewrite_hash(None), path="A-baseline", reason="missing")]
    assert rw.done_qids(rows, "c1", rewrites) == {"a", "b"}
    assert list(rw.current_rows(rows, "c1")) == ["a", "b", "d", "e", "f", "g"]
    assert rw.current_rows(rows, "c1")["b"] is rows[4]
    assert [r["qid"] for r in rw.fresh_rows(rows, "c1", rewrites)] == ["a", "b", "d", "f", "g"]  # e left out
    assert rw.is_done(row("x", path="A-fallback", reason="none", rh=rw.rewrite_hash({"status": "none"})),
                      {"status": "none"})


def test_rw_eval_off_and_tau_rows_need_no_rewrite():
    used = {"status": "used", "rewrite": "전입자 수"}

    def row(qid, reason, path, err=None):
        return {"qid": qid, "config_id": "c1", "stage2_error": err, "rewrite_hash": rw.rewrite_hash(None),
                "path": path, "fallback_reason": reason}

    rows = [row("a", "off", "A"), row("b", "tau", "A-fallback"), row("c", "tau", "A-baseline", err="HTTP 500")]
    rewrites = {q: used for q in "abc"}  # a rewrite exists now: off / tau rows were made without one, still done
    assert rw.done_qids(rows, "c1", rewrites) == {"a", "b"}  # c: a stage 2 error is asked again
    assert [r["qid"] for r in rw.fresh_rows(rows, "c1", rewrites)] == ["a", "b", "c"]
    assert rw.is_done(row("x", "missing", "A-fallback"), None) is False  # waits for a rewrite


def test_rw_eval_refuses_missing_rewrites():
    rewrites = {"a": {"status": "used", "rewrite": "x"}}
    with pytest.raises(SystemExit, match="no R2 rows"):
        rw.check_rewrites({}, [_item("a")], variant="R2", build="v2", allow_missing=False)
    items = [_item("a"), _item("secret-qid"), _item("t1", split="test")]
    with pytest.raises(SystemExit, match="dev 1/2, test 1/1") as exc:
        rw.check_rewrites(rewrites, items, variant="R2", build="v2", allow_missing=False)
    assert "secret-qid" not in str(exc.value) and "t1" not in str(exc.value)  # counts only
    assert rw.check_rewrites(rewrites, items, variant="R2", build="v2", allow_missing=True) == 2
    assert rw.check_rewrites(rewrites, [_item("a")], variant="R2", build="v2", allow_missing=False) == 0


def test_rw_eval_path_a_reuses_the_baseline_row():
    item = _item("a", required=(("K",),))
    base = {"qid": "a", "keys": ["X", "K"] + [f"Y{i}" for i in range(12)], "stage2_picked_n": 2, "stage2_error": None,
            "stage2_reason": None, "stage2_calls": 1, "stage1_top_score": 0.4, "stage1_margin": 0.1, "latency_ms": 900.0}
    rewrite = {"status": "rejected", "rewrite": "x", "seconds": 4.0}
    # no graph and no resources: a path A item with a baseline row never searches nor calls the LLM
    row = rw.run_item(item, rw.Setup(graph=None, resources=None, baseline={"a": base}, rewrites={"a": rewrite}))
    assert row["path"] == "A-baseline" and row["fallback_reason"] == "rejected" and row["rewrite_status"] == "rejected"
    assert row["keys"] == base["keys"][:10] and row["rank"] == 2 and row["route"] == "found"
    assert (row["stage2_picked_n"], row["stage2_calls"], row["stage1_top_score"]) == (2, 1, 0.4)
    assert (row["search_ms"], row["rewrite_ms"], row["latency_ms"]) == (900.0, 4000.0, 4900.0)
    assert row["rewrite_hash"] == rw.rewrite_hash(rewrite)
    refused = rw.from_baseline(_item("n", answerable=False), None, {"keys": ["X"], "stage2_picked_n": 0}, "missing", "R2")
    assert refused["route"] == "refuse" and refused["no_answer_pred"] and refused["latency_ms"] is None
    assert refused["rank"] is None and refused["rewrite_status"] == "missing"


def test_rw_eval_search_identity_covers_the_search_and_stage2():
    from types import SimpleNamespace

    from kosis_rag.schemas.resources import ChromaConfig, IndexSpec, SearchOptions

    spec = IndexSpec(chroma=ChromaConfig(collection="kosis_mixft_E2s_e2"), embed_ckpt="e.pt", rerank_ckpt="r.pt")
    llm = SimpleNamespace(thinking="none", model="gemma", base_url="http://local")
    res = SimpleNamespace(spec=spec, options=SearchOptions(cond_scope="years", stage2="gpt"), stage2_llm=llm,
                          text_format="v2")
    ident = rw.search_identity(res)
    for key in ("collection", "embed_ckpt", "rerank_ckpt", "hq_build", "text_format", "pool", "rerank_n",
                "rerank_mode", "stage2_thinking", "stage2_model", "stage2_base_url", "cond_scope"):
        assert key in ident
    assert (ident["collection"], ident["cond_scope"], ident["stage2_model"]) == ("kosis_mixft_E2s_e2", "years", "gemma")
    other = rw.search_identity(SimpleNamespace(**{**vars(res), "options": SearchOptions(cond_scope="years", pool=30)}))
    assert rw.config_id({"search": ident}) != rw.config_id({"search": other})


def _rw_fixture():
    golden = {i.qid: i for i in (_item("d1", required=(("K1",),)), _item("d2", required=(("K2",),)),
                                 _item("d3", required=(("K3",),)), _item("n1", answerable=False),
                                 _item("n2", answerable=False), _item("t1", split="test", required=(("K9",),)))}
    meta = {"d1": {"style": "S0"}, "d2": {"style": "S1"}, "d3": {"style": "S1"}, "n1": {"style": "S0"}}

    def row(qid, path, keys, reason=None, picked=1, status="used"):
        return {"qid": qid, "split": "dev", "path": path, "fallback_reason": reason, "rewrite_status": status,
                "keys": keys, "stage2_picked_n": picked, "stage2_error": None, "search_ms": 1000.0,
                "rewrite_ms": 4000.0, "latency_ms": 5000.0, "protected_added": 1 if qid == "d2" else 0}

    rows = [row("d1", "B", ["K1"]), row("d2", "B", ["X", "K2"]), row("d3", "A-fallback", ["K3"], "rejected", 1, "rejected"),
            row("n1", "B", ["X"], picked=0), row("n2", "A-fallback", ["X"], "none", 1, "none")]
    base = [{"qid": "d1", "keys": ["X", "K1"], "stage2_picked_n": 1, "latency_ms": 900.0},
            {"qid": "d2", "keys": ["K2"], "stage2_picked_n": 1, "latency_ms": 900.0},
            {"qid": "d3", "keys": ["K3"], "stage2_picked_n": 1, "latency_ms": 900.0},
            {"qid": "n1", "keys": ["X"], "stage2_picked_n": 1, "latency_ms": 900.0},
            {"qid": "n2", "keys": [], "latency_ms": 900.0},
            {"qid": "t1", "keys": ["K9"], "latency_ms": 900.0}]  # a test row of the baseline run: never paired
    return golden, meta, rows, base


def test_rw_eval_summary_and_paired_comparison():
    golden, meta, rows, base = _rw_fixture()
    s = rw.summarize(rows, golden, meta, "dev", base, n_expected=5)
    g = s["groups"]
    assert g["all"]["all"]["hit@1"] == pytest.approx(2 / 3) and g["all"]["all"]["n"] == 5
    assert set(g["path"]) == {"A-fallback", "B"} and g["path"]["B"]["n"] == 3
    assert set(g["rewrite_status"]) == {"used", "rejected", "none"} and set(g["style"]) == {"S0", "S1", "-"}
    assert s["paths"] == {"A-fallback": 2, "B": 3} and s["fallback_reasons"] == {"none": 1, "rejected": 1}
    assert s["protected"] == {"items": 1, "tables": 1} and s["stage2_errors"] == 0
    assert s["latency"]["search_ms"]["p50"] == 1000.0 and s["latency"]["latency_ms"]["n"] == 5
    assert s["baseline"]["n_graded"] == 5 and s["baseline"]["groups"]["all"]["all"]["hit@1"] == pytest.approx(2 / 3)
    p = s["paired"]["all"]
    assert (p["n"], p["n_answerable"], p["n_no_answer"]) == (5, 3, 2)
    assert p["hit1"] == {"rw": 2, "base": 2, "up": 1, "down": 1} and p["rank"] == {"better": 1, "worse": 1}
    assert p["mrr_delta"] == pytest.approx(0.0)
    assert p["wrong_answer"] == {"rw": 1, "base": 1, "fixed": 1, "broken": 1}
    assert p["answerable_refused"] == {"rw": 0, "base": 0}
    assert s["dev_check"] == {"hit1_net_drop": 0, "wrong_answer_net_rise": 0, "complete": True, "n_expected": 5,
                              "n_paired": 5, "stage2_errors": 0, "search_failed": 0, "pass": True}
    b = s["paired"]["path_B"]
    assert b["n"] == 3 and b["wrong_answer"] == {"rw": 0, "base": 1, "fixed": 1, "broken": 0}
    assert "dev_check" not in b  # the plan's check is over all items only
    check = lambda r=rows, **kw: rw.summarize(r, golden, meta, "dev", base, **{"n_expected": 5, **kw})["dev_check"]  # noqa: E731
    worse = [dict(r, keys=["X"]) if r["qid"] in ("d1", "d3") else r for r in rows]  # two Hit@1 lost
    assert check(worse)["hit1_net_drop"] == 2 and check(worse)["pass"] is False
    answered = [dict(r, stage2_picked_n=1) if r["qid"] == "n1" else r for r in rows]  # +1 wrong answer: allowed
    assert check(answered)["wrong_answer_net_rise"] == 1 and check(answered)["pass"] is True
    assert check(n_expected=6)["complete"] is False and check(n_expected=6)["pass"] is False  # an item not run
    assert check(n_expected=None)["pass"] is False
    assert check(search_failed=1)["pass"] is False
    errored = [dict(r, stage2_error="TimeoutError") if r["qid"] == "d2" else r for r in rows]
    assert check(errored)["stage2_errors"] == 1 and check(errored)["pass"] is False
    assert "paired" not in rw.summarize(rows, golden, meta, "dev") and "dev_check" not in rw.summarize(rows, golden, meta, "dev")


def test_rw_eval_report_prints_no_question_and_no_key():
    golden, meta, rows, base = _rw_fixture()
    s = rw.summarize(rows, golden, meta, "dev", base, n_expected=5)
    s.update(split="dev", label="rw_eval path B")
    text = rw.render(s, "v2-condyears/rw_eval/q3", "B1-run")
    assert "비밀 질문" not in text and not any(k in text for k in ("K1", "K2", "K3", "K9"))
    assert sum(1 for line in text.split("\n") if line.startswith("# ")) == 1  # one H1
    assert text.index("## 경로·지연") < text.index("B1-run") < text.index("## 문항 짝 비교")  # baseline after 경로·지연
    assert text.count("dev 판정") == 1 and text.index("## dev 판정") > text.index("(path_B")
    assert "판정: 통과" in text
    s2 = rw.summarize(rows, golden, meta, "dev", base, n_expected=6)
    s2.update(split="dev", label="rw_eval path B")
    assert "판정: 미달" in rw.render(s2, "q3", "B1-run") and "(미완료)" in rw.render(s2, "q3", "B1-run")


def test_rw_eval_summary_per_split():
    golden, meta, rows, base = _rw_fixture()
    test_row = {**rows[0], "qid": "t1", "split": "test", "keys": ["K9"], "stage2_error": "TimeoutError",
                "latency_ms": 7000.0}
    by = rw.summarize_splits(rows + [test_row], golden, meta, "all", base, n_expected={"dev": 5, "test": 1})
    assert list(by) == ["dev", "test", "all"] and [s["split"] for s in by.values()] == ["dev", "test", "all"]
    assert (by["dev"]["n_graded"], by["test"]["n_graded"], by["all"]["n_graded"]) == (5, 1, 6)
    # every count is over the split's own rows: the test row's path, latency and stage 2 error stay out of dev
    assert by["test"]["paths"] == {"B": 1} and by["dev"]["paths"] == {"A-fallback": 2, "B": 3}
    assert by["all"]["paths"] == {"A-fallback": 2, "B": 4}
    assert [by[s]["latency"]["latency_ms"]["n"] for s in ("dev", "test", "all")] == [5, 1, 6]
    assert by["dev"]["latency"]["latency_ms"]["p95"] == 5000.0 and by["test"]["latency"]["latency_ms"]["p50"] == 7000.0
    assert (by["dev"]["stage2_errors"], by["test"]["stage2_errors"], by["all"]["stage2_errors"]) == (0, 1, 1)
    assert by["dev"]["protected"] == {"items": 1, "tables": 1} and by["test"]["protected"] == {"items": 0, "tables": 0}
    assert by["dev"]["fallback_reasons"] == {"none": 1, "rejected": 1} and by["test"]["fallback_reasons"] == {}
    assert "dev_check" in by["dev"] and by["dev"]["dev_check"]["pass"] is True
    assert by["dev"]["dev_check"]["stage2_errors"] == 0  # the test row's error is not a dev error
    assert "dev_check" not in by["test"] and "dev_check" not in by["all"]  # the dev check is a dev judgement only
    assert by["test"]["paired"]["all"]["n"] == 1 and by["all"]["paired"]["all"]["n"] == 6
    assert list(rw.summarize_splits(rows, golden, meta, "dev", base, n_expected={"dev": 5})) == ["dev"]
    for s in by.values():
        s["label"] = "rw_eval rewrite on"
    text = rw.render_splits(by, "v3-condyears-p2/rw_eval/q5", "B1-run")
    assert sum(1 for line in text.split("\n") if line.startswith("# ")) == 1  # one H1 over the three splits
    assert text.count("dev 판정") == 1 and "[test]" in text and "[all]" in text
    assert "비밀 질문" not in text and not any(k in text for k in ("K1", "K2", "K3", "K9"))


def test_rw_eval_console_shows_a_counter_only():
    source = (FOLDER / "rw_eval.py").read_text(encoding="utf-8")
    prints = [line.strip() for line in source.splitlines() if line.strip().startswith("print(f\"[{n}/")]
    assert prints and all("qid" not in p and "path" not in p and "route" not in p for p in prints)


def test_rw_eval_config_id_ignores_urls_and_path_spelling(tmp_path):
    import copy

    config = {"rewrite": "on", "model": "gemma", "base_url": "http://127.0.0.1:8001/v1",
              "rewriter": {"prompt": "rewrite-v2.md", "model": "gemma", "base_url": "http://127.0.0.1:8001/v1"},
              "search": {"collection": "c", "embed_ckpt": "data\\models\\embedder\\E2s-e2.pt",
                         "rerank_ckpt": "data/models/reranker/RR-E2s-e2.pt", "stage2_model": "gemma",
                         "stage2_base_url": "http://127.0.0.1:8001/v1"}}
    moved = copy.deepcopy(config)  # a restarted tunnel on another port, the checkpoints spelled another way
    moved["base_url"] = moved["rewriter"]["base_url"] = moved["search"]["stage2_base_url"] = "http://127.0.0.1:9000/v1"
    moved["search"]["embed_ckpt"] = "D:/repo/data/models/embedder/e2s-e2.pt"
    cid, legacy = rw.config_ids(config)
    assert legacy == rw.config_id(config)  # the formula of the rows written before
    assert rw.config_ids(moved)[0] == cid and rw.config_ids(moved)[1] != legacy
    stable = rw.stable_config(config)
    assert "base_url" not in stable and "base_url" not in stable["rewriter"] and "stage2_base_url" not in stable["search"]
    assert stable["search"]["embed_ckpt"] == "e2s-e2.pt" and stable["model"] == stable["search"]["stage2_model"] == "gemma"
    assert config["search"]["embed_ckpt"].startswith("data")  # the input is not changed
    other = copy.deepcopy(config)
    other["search"]["stage2_model"] = "gemma-2"
    assert rw.config_ids(other)[0] != cid  # model names still count
    rows = [{"qid": "a", "config_id": legacy}, {"qid": "b", "config_id": cid}, {"qid": "c", "config_id": "zzz"}]
    assert list(rw.current_rows(rows, (cid, legacy))) == ["a", "b"] and list(rw.current_rows(rows, cid)) == ["b"]
    assert rw.done_qids([dict(r, fallback_reason="off") for r in rows], (cid, legacy), {}) == {"a", "b"}
    assert rw.summary_ids({"config_id": legacy}) == [legacy] and rw.summary_ids({}) == []
    assert rw.summary_ids({"config_id": cid, "config_ids": [cid, legacy]}) == [cid, legacy]
    assert lib.ckpt_name(None) is None and lib.ckpt_name("") is None and lib.ckpt_name("A\\B.pt") == "b.pt"
    stage = tmp_path / "q5"
    stage.mkdir()
    (stage / "summary.json").write_text(json.dumps({"config_id": cid, "config_ids": [cid, legacy],
                                                    "config": {"rewrite": "off", "model": "gemma"}}), encoding="utf-8")
    (stage / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert [r["qid"] for r in rw.load_baseline(stage)] == ["a", "b"]  # the legacy rows are read too
    assert rw.baseline_stage2_model(stage) == "gemma"
    l1 = tmp_path / "l1"
    l1.mkdir()
    (l1 / "manifest.json").write_text(json.dumps({"stage2_llm": {"model": "gpt-x", "base_url": "u"}}), encoding="utf-8")
    assert rw.baseline_stage2_model(l1) == "gpt-x"
    (l1 / "manifest.json").write_text(json.dumps({"llm": None}), encoding="utf-8")  # a run before T3-23: not said
    assert rw.baseline_stage2_model(l1) is None and rw.baseline_stage2_model(tmp_path) is None


def test_bare_presets_with_a_cond_scope_run_only_as_graph():
    from evals.runners.retrieval import GRAPH, build

    with pytest.raises(ValueError, match="graph:staged-condyears"):
        build("staged-condyears", [], 10)  # refused before anything is loaded
    with pytest.raises(ValueError, match="graph:staged-gpt-condyears"):
        build("staged-gpt-condyears", [], 10)
    assert GRAPH == "graph:"


# --- trigger_select.py (plan 07 Q4 + Q5) ----------------------------------------------------------------------------

ts = _script("trigger_select")


def test_trigger_select_selection_rule():
    base = {"a": {"keys": ["A"], "stage1_top_score": 0.1, "latency_ms": 1000.0},
            "b": {"keys": ["B"], "stage1_top_score": 0.1, "latency_ms": 1000.0},
            "c": {"keys": ["C"], "stage1_top_score": 0.9, "latency_ms": 1000.0},
            "d": {"keys": ["D"], "latency_ms": 1000.0},  # no score in the baseline row: the on-run row's
            "e": {"keys": ["E"], "stage1_top_score": 0.1, "latency_ms": 1000.0}}  # no on-run row
    on = {"a": {"path": "B", "keys": ["A2"], "latency_ms": 3000.0, "rewrite_ms": 1000.0},
          "b": {"path": "A-baseline", "keys": ["B"], "latency_ms": 5000.0, "rewrite_ms": 4000.0},
          "c": {"path": "B", "keys": ["C2"], "latency_ms": 3000.0, "rewrite_ms": 1000.0},
          "d": {"path": "B", "keys": ["D2"], "stage1_top_score": 0.05, "latency_ms": 3000.0, "rewrite_ms": 1000.0}}
    rows = {r["qid"]: r for r in ts.select_rows(on, base, 0.5)}
    pick = {q: (r["choice"], r["keys"], r["latency_ms"], r["triggered"]) for q, r in rows.items()}
    assert pick == {"a": ("B", ["A2"], 3000.0, True),  # triggered, rewrite used: the path B row
                    "b": ("A+rw", ["B"], 5000.0, True),  # triggered, not used: baseline result + the rewrite's time
                    "c": ("A", ["C"], 1000.0, False),  # not below tau: the baseline row
                    "d": ("B", ["D2"], 3000.0, True),
                    "e": ("A", ["E"], 1000.0, False)}
    always = {r["qid"]: r["choice"] for r in ts.select_rows(on, base, math.inf)}
    assert always == {"a": "B", "b": "A+rw", "c": "B", "d": "B", "e": "A"}
    assert base["b"]["latency_ms"] == 1000.0 and "choice" not in on["a"]  # the inputs are not changed
    assert ts.fires(None, math.inf) and not ts.fires(None, 0.5) and not ts.fires(0.5, 0.5) and ts.fires(0.4, 0.5)
    assert ts.trigger_score({"stage1_top_score": 0.3}, {"stage1_top_score": 0.7}) == 0.3
    assert ts.trigger_score(None, {"stage1_top_score": 0.7}) == 0.7 and ts.trigger_score({}, None) is None


def test_trigger_select_candidates_rejections_and_choice():
    cands = ts.candidates([i / 20 for i in range(1, 21)])
    assert [c["label"] for c in cands] == ["20%", "35%", "50%", "always"]
    assert [c["tau"] for c in cands[:3]] == pytest.approx([0.225, 0.375, 0.525]) and math.isinf(cands[3]["tau"])
    assert [c["label"] for c in ts.candidates([])] == ["always"]

    def dev(mrr, rate=0.2, rise=0.0, trig=500.0):
        return {"mrr": mrr, "trigger_rate": rate, "p50_rise_ms": rise, "triggered_p50_rise_ms": trig}

    assert ts.rejections(dev(0.80), 0.75) == []
    assert ts.rejections(dev(0.75), 0.75) == ["mrr"]  # equal is not an effect
    assert ts.rejections(dev(0.80, rise=1501.0, trig=3001.0), 0.75) == ["p50", "triggered_p50"]
    assert ts.rejections(dev(0.80, rise=1500.0, trig=3000.0), 0.75) == []  # the limits themselves pass
    assert ts.rejections(dev(None, rise=None, trig=None), 0.75) == ["mrr_unmeasured", "p50_unmeasured",
                                                                     "triggered_p50_unmeasured"]
    assert ts.rejections(None, 0.75) == ["no_dev"] and set(ts.REJECT_TEXT) >= {"no_dev", "mrr", "p50"}

    def cand(label, rate, rejected=()):
        return {"label": label, "dev": {"trigger_rate": rate}, "rejected": list(rejected)}

    # plan Q4: the lowest rate that has an effect, not the highest MRR
    assert ts.choose([cand("20%", 0.2, ["mrr"]), cand("35%", 0.35), cand("always", 1.0)])["label"] == "35%"
    assert ts.choose([cand("50%", 0.4), cand("35%", 0.4)])["label"] == "50%"  # equal rates: the earlier
    assert ts.choose([cand("20%", 0.2, ["mrr"]), cand("always", 1.0, ["p50"])]) is None


def test_trigger_select_bootstrap_is_seeded():
    diffs = [0.5, 0.0, 0.5, -0.25, 1.0, 0.0, 0.5]
    a = ts.paired_bootstrap(diffs, resamples=2000, seed=7)
    assert a == ts.paired_bootstrap(diffs, resamples=2000, seed=7)  # the same seed, the same CI
    assert a["mean"] == pytest.approx(sum(diffs) / len(diffs)) and a["lo"] < a["mean"] < a["hi"]
    assert (a["n"], a["resamples"], a["seed"], a["ci"]) == (7, 2000, 7, 0.95)
    assert ts.paired_bootstrap([0.2] * 5, resamples=100)["lo"] == pytest.approx(0.2)
    assert ts.paired_bootstrap([])["lo"] is None and ts.BOOTSTRAP_RESAMPLES == 10_000


def _ts_ev(**changes):
    ev = {"style": {"S0": {"hit@1": 0.5, "n_answerable": 10}},
          "baseline_style": {"S0": {"hit@1": 0.6, "n_answerable": 10}},
          "selected": {"no_answer": {"f1": 0.80}}, "baseline": {"no_answer": {"f1": 0.82}}, "trigger": {"n": 5},
          "latency": {"p50": 2500.0, "base_p50": 1000.0, "triggered_p50": 4000.0, "triggered_base_p50": 1000.0,
                      "n_triggered": 5}}
    for path, value in changes.items():
        *parents, leaf = path.split(".")
        node = ev
        for p in parents:
            node = node[p]
        node[leaf] = value
    return ev


def test_trigger_select_criteria():
    def verdicts(boot_lo=0.001, **changes):
        return {c["name"]: c["pass"] for c in ts.criteria(_ts_ev(**changes), {"lo": boot_lo})}

    ok = verdicts()
    assert ok == {"a_mrr_ci_lo": True, "b_s0_hit1_net_drop": True, "c_no_answer_f1_drop": True, "d_p50_rise_ms": True,
                  "d_triggered_p50_rise_ms": True}  # every limit met, (d) exactly at 1.5 s and 3 s
    assert ts.overall(ts.criteria(_ts_ev(), {"lo": 0.001}))
    assert verdicts(boot_lo=0.0)["a_mrr_ci_lo"] is False  # the lower bound must be above 0
    assert verdicts(boot_lo=None)["a_mrr_ci_lo"] is None
    assert verdicts(**{"style.S0.hit@1": 0.4})["b_s0_hit1_net_drop"] is True  # 6 -> 4: a net drop of 2
    assert verdicts(**{"style.S0.hit@1": 0.3})["b_s0_hit1_net_drop"] is False  # 6 -> 3
    assert verdicts(**{"selected.no_answer.f1": 0.79})["c_no_answer_f1_drop"] is True  # .03
    assert verdicts(**{"selected.no_answer.f1": 0.78})["c_no_answer_f1_drop"] is False  # .04
    assert verdicts(**{"latency.p50": 2501.0})["d_p50_rise_ms"] is False
    assert verdicts(**{"latency.triggered_p50": 4001.0})["d_triggered_p50_rise_ms"] is False
    # nothing triggered: no rise; triggered items but none with a latency: cannot be measured
    assert verdicts(**{"trigger.n": 0, "latency.n_triggered": 0, "latency.triggered_p50": None})[
        "d_triggered_p50_rise_ms"] is True
    assert verdicts(**{"latency.n_triggered": 0, "latency.triggered_p50": None})["d_triggered_p50_rise_ms"] is None
    unmeasured = ts.criteria(_ts_ev(**{"selected.no_answer.f1": None}), {"lo": 0.001})
    assert [c["pass"] for c in unmeasured if c["name"] == "c_no_answer_f1_drop"] == [None]
    assert ts.overall(unmeasured) is False  # a criterion that cannot be measured fails the whole
    assert ts.overall(ts.criteria(_ts_ev(**{"latency.p50": 9999.0}), {"lo": 0.001})) is False


def _ts_fixture(on_latency=2000.0):
    items = [_item("q-d1", required=(("K1",),)), _item("q-d2", required=(("K2",),)), _item("q-d3", required=(("K3",),)),
             _item("q-d4", required=(("K4",),)), _item("q-dn", answerable=False),
             _item("q-t1", split="test", required=(("K5",),)), _item("q-t2", split="test", required=(("K6",),)),
             _item("q-t3", split="test", required=(("K7",),)), _item("q-t4", split="test", required=(("K8",),)),
             _item("q-tn", split="test", answerable=False)]
    golden = {i.qid: i for i in items}
    meta = {q: {"style": s} for q, s in (("q-d1", "S0"), ("q-d2", "S1"), ("q-d3", "S1"), ("q-d4", "S0"),
                                         ("q-t1", "S0"), ("q-t2", "S0"), ("q-t3", "S1"), ("q-t4", "S1"))}

    def b(keys, score, picked=1):
        return {"keys": keys, "stage1_top_score": score, "stage2_picked_n": picked, "stage2_error": None,
                "latency_ms": 1000.0}

    def o(keys, path="B", picked=1, latency=on_latency, rewrite_ms=1000.0):
        return {"path": path, "keys": keys, "stage2_picked_n": picked, "stage2_error": None, "latency_ms": latency,
                "rewrite_ms": rewrite_ms, "rewrite_status": "used" if path == "B" else "rejected"}

    base = {"q-d1": b(["X", "K1"], 0.1), "q-d2": b(["X", "K2"], 0.2), "q-d3": b(["K3"], 0.8), "q-d4": b(["K4"], 0.9),
            "q-dn": b(["X"], 0.05, picked=0),
            "q-t1": b(["X", "K5"], 0.1), "q-t2": b(["K6"], 0.9), "q-t3": b(["X", "K7"], 0.12),
            "q-t4": b(["X", "K8"], 0.13), "q-tn": b(["X"], 0.5, picked=0)}
    on = {"q-d1": o(["K1"]), "q-d2": o(["K2"]), "q-d3": o(["X", "K3"]),
          "q-d4": o(["K4"], path="A-baseline", latency=5000.0, rewrite_ms=4000.0), "q-dn": o(["X"]),
          "q-t1": o(["K5"]), "q-t2": o(["X"]), "q-t3": o(["K7"]), "q-t4": o(["K8"]), "q-tn": o(["X"])}
    return golden, meta, base, on


def test_trigger_select_chooses_the_lowest_effective_rate_and_judges_test():
    golden, meta, base, on = _ts_fixture()
    out = ts.select_and_judge(on, base, golden, meta, resamples=2000)
    cands = {c["label"]: c for c in out["candidates"]}
    assert list(cands) == ["20%", "35%", "50%", "always"]
    # dev scores .05 .1 .2 .8 .9: 20% -> tau .075 (q-dn only), 35% and 50% (round(2.5) = 2) -> tau .15
    assert cands["20%"]["tau"] == pytest.approx(0.075) and cands["35%"]["tau"] == pytest.approx(0.15)
    assert cands["20%"]["dev"]["mrr"] == pytest.approx(0.75) and cands["20%"]["rejected"] == ["mrr"]  # no effect
    assert cands["35%"]["dev"]["mrr"] == pytest.approx(0.875) and cands["35%"]["dev"]["trigger_rate"] == 0.4
    assert (cands["35%"]["dev"]["p50_rise_ms"], cands["35%"]["dev"]["triggered_p50_rise_ms"]) == (0.0, 1000.0)
    assert cands["35%"]["rejected"] == [] and cands["always"]["rejected"] == []
    assert cands["always"]["dev"]["trigger_rate"] == 1.0 and cands["always"]["dev"]["b_rate"] == 0.8
    assert out["chosen"] == {"label": "35%", "tau": pytest.approx(0.15), "forced": False}  # the lowest effective rate
    assert out["baseline_dev"]["mrr"] == pytest.approx(0.75) and out["baseline_dev"]["trigger_rate"] == 0.0
    clean = {"n_golden": 5, "n_judged": 5, "base_missing": 0, "on_missing": 0, "on_stage2_errors": 0,
             "on_rewrite_errors": 0, "base_stage2_errors": 0}
    assert out["coverage"] == {"dev": clean, "test": clean} and out["blocked"] == []
    t = out["test"]
    assert (t["n"], t["n_answerable"], t["trigger"]["n"], t["trigger"]["n_b"]) == (5, 4, 3, 3)
    assert t["selected"]["mrr"] == pytest.approx(1.0) and t["baseline"]["mrr"] == pytest.approx(0.625)
    assert t["paired"]["hit1"] == {"rw": 4, "base": 1, "up": 3, "down": 0}
    assert t["paired"]["answerable_refused"] == {"rw": 0, "base": 0}
    assert t["mrr_refused0"] == {"selected": pytest.approx(1.0), "baseline": pytest.approx(0.625)}
    assert (t["latency"]["p50"], t["latency"]["triggered_p50"], t["latency"]["triggered_base_p50"]) == (
        2000.0, 2000.0, 1000.0)
    assert out["bootstrap"]["mean"] == pytest.approx(0.375) and out["bootstrap"]["lo"] > 0
    assert all(c["pass"] for c in out["criteria"]) and out["pass"] is True
    assert ts.select_and_judge(on, base, golden, meta, resamples=2000)["bootstrap"] == out["bootstrap"]  # seeded
    forced = ts.select_and_judge(on, base, golden, meta, tau=0.075, resamples=200)
    assert forced["chosen"] == {"label": "--tau", "tau": 0.075, "forced": True} and forced["test"]["trigger"]["n"] == 0
    assert forced["candidates"][-1]["rejected"] == ["mrr"]  # shown, judged anyway
    assert forced["pass"] is False  # nothing triggers on test: the MRR CI is [0, 0]


def test_trigger_select_without_an_effective_tau_does_not_judge_test():
    golden, meta, base, on = _ts_fixture(on_latency=9000.0)  # every rewrite costs 8 s more than the baseline
    out = ts.select_and_judge(on, base, golden, meta, resamples=200)
    cands = {c["label"]: c["rejected"] for c in out["candidates"]}
    assert cands == {"20%": ["mrr", "triggered_p50"], "35%": ["triggered_p50"], "50%": ["triggered_p50"],
                     "always": ["p50", "triggered_p50"]}
    assert out["chosen"] is None and out["test"] is None and out["pass"] is None and out["criteria"] == []
    text = ts.render({"on_run": "q5", "baseline_run": "B1-run", **out})
    assert "적합한 τ 없음" in text and "Q5 test 판정" not in text and "트리거 p50 증가 > 3 s" in text
    forced = ts.select_and_judge(on, base, golden, meta, tau=0.15, resamples=200)
    assert forced["chosen"]["forced"] is True and forced["test"] is not None and forced["pass"] is False  # (d)
    assert "--tau 지정" in ts.render({"on_run": "q5", "baseline_run": "B1-run", **forced})


def test_trigger_select_missing_or_failed_test_items_block_the_verdict():
    golden, meta, base, on = _ts_fixture()
    on = {**on, "q-t3": {**on["q-t3"], "stage2_error": "TimeoutError"},
          "q-t2": {**on["q-t2"], "path": "A-baseline", "rewrite_status": "error", "stage2_error": "baseline's"}}
    del on["q-t1"]
    base = {q: r for q, r in base.items() if q != "q-t4"}
    out = ts.select_and_judge(on, base, golden, meta, resamples=200)
    c = out["coverage"]["test"]
    assert (c["n_golden"], c["n_judged"], c["base_missing"], c["on_missing"]) == (5, 4, 1, 1)
    assert (c["on_stage2_errors"], c["on_rewrite_errors"]) == (1, 1)  # an A-baseline row's error is the baseline's
    assert out["blocked"] == ["base_missing", "on_missing", "on_stage2_errors", "on_rewrite_errors"]
    assert out["test"] is not None and out["criteria"] and out["pass"] is None  # judged, but 판정 불가
    text = ts.render({"on_run": "q5", "baseline_run": "B1-run", **out})
    assert "종합: 판정 불가 (test 누락·오류: 기준선 없음 1, on-run 없음 1" in text
    assert out["coverage"]["dev"]["on_missing"] == 0 and "q-" not in text


def test_trigger_select_forced_tau_without_dev_items_renders():
    golden, meta, base, on = _ts_fixture()
    test_only = {q: r for q, r in base.items() if golden[q].split == "test"}
    with pytest.raises(SystemExit, match="--tau"):
        ts.select_and_judge(on, test_only, golden, meta, resamples=10)
    out = ts.select_and_judge(on, test_only, golden, meta, tau=0.15, resamples=200)
    assert out["baseline_dev"] is None and all(c["dev"] is None for c in out["candidates"])
    assert out["candidates"][-1]["rejected"] == ["no_dev"] and out["chosen"]["forced"] is True
    text = ts.render({"on_run": "q5", "baseline_run": "B1-run", **out})  # no TypeError without dev numbers
    assert "dev 문항 없음" in text and "Q5 test 판정" in text


def test_trigger_select_output_has_no_qid_and_one_h1():
    golden, meta, base, on = _ts_fixture()
    out = {"on_run": "q5", "baseline_run": "B1-run", **ts.select_and_judge(on, base, golden, meta, resamples=500)}
    text, body = ts.render(out), json.dumps(lib.json_safe(out), ensure_ascii=False)
    for written in (text, body):
        assert "q-" not in written and "비밀 질문" not in written  # aggregates only
        assert not any(f'"K{i}"' in written or f" K{i} " in written for i in range(1, 9))
    assert "rr_diffs" not in out["test"]  # per-item differences are never written
    assert sum(1 for line in text.split("\n") if line.startswith("# ")) == 1
    assert "종합: 통과" in text and text.count("→ 통과") == 5 and "35% ✓" in text
    assert "답 없음 잘못 답함 0 → 0" in text and "답 있음 거절 0 → 0" in text and "민감도" in text
    assert "## 문항 수 확인" in text and "| test | 5 | 5 | 0 | 0 | 0 | 0 | 0 |" in text
    dev_only = {q: r for q, r in base.items() if golden[q].split == "dev"}
    none = {"on_run": "q5", "baseline_run": "B1-run", **ts.select_and_judge(on, dev_only, golden, meta, resamples=10)}
    assert none["test"] is None and none["pass"] is None and "판정하지 않음" in ts.render(none)
    assert none["blocked"] == ["base_missing"]  # the test rows are missing from the baseline
