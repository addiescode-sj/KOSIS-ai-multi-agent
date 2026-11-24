"""KOS-192 Q3 / Q5 (plan 07): the rewrite path B in the fixed H1 configuration, with the rewrite made inline, in one run.

    set PYTHONPATH=.;src;vendor\\phase2\\src;vendor\\phase1;vendor\\phase1\\src
    python scripts\\kos192\\rw_eval.py --collection <collection> --embed-ckpt <embedder .pt> --rerank-ckpt <re-ranker .pt> --hq-build "" [--rewrite on|off] [--rewrite-tau 0.05] [--split dev|test|all --allow-test] [--rewrite-prompt v2|v1] [--build v3-condyears-p2] [--stage q3] [--baseline-run evals\\reports\\<B1 run>] [--limit 3]

--rewrite on (default) runs, per question of the split (dev; test and all need --allow-test):
  1. pass 1: the original question through the search graph up to stage 1 (kos192_lib.stage1_pass) with the search of
     --preset (default staged-condyears, the B1 baseline's search): pass 1's lists, table filter and stage 1 order.
     With --rewrite-tau, a question whose pass 1 stage 1 top score is not below tau takes path A (reason "tau") and
     no rewrite is made; without it every question is rewritten ("always").
  2. the rewrite (--variant, default R2): made inline with gen_rewrites' own code (rewrite_checked: the prompt of
     --rewrite-prompt, default v2 = rewrite-v2.md, v1 = rewrite-v1.md; the stage 2 "gpt" server or --rewrite-model /
     --rewrite-base-url; parse + check_rewrite; R2 shows the names of pass 1's top 10 tables) and cached in
     data/lab/rewrite/<build>/rewrites.jsonl in gen_rewrites' row format (split, preset, prompt, prompt_sha, model;
     plus wall_seconds, the measured rewrite step). A cached row of the same prompt sha, model, preset and variant that
     is not an error is reused instead of calling the model again (cached_rewrites); a row of another prompt never is.
     --build defaults per prompt (REWRITE_BUILDS: v1 -> v2-condyears, where the Q1-Q3 v1 rows are; v2 ->
     v3-condyears-p2), so the two prompts' rows and runs stay in separate folders.
     --rewrites-from <build> reads that build's rows of the same prompt sha instead and makes none (source_rewrites; a
     question without a row is refused unless --allow-missing; it then takes path A, reason "missing").
  3. path "B" when the rewrite is "used" (the model gave one and check_rewrite passed it):
     a. lists: the rewrite searched in dense and BM25 under the ORIGINAL question's table filter, fused with pass 1's
        lists by weighted RRF (pass 1's lists their own weight 1, the rewrite lists --rw-weight, default 2) into a pool
        of options.pool (50): kos192_lib.search_rewrite / fuse_rewrite, the functions recall_compare uses;
     b. rerank1: the stage 1 cross-encoder scores the pool's top options.rerank_n twice (original question, rewrite);
        the two score orders are fused by RRF (1/(c + rank_q) + 1/(c + rank_rw), c = RRF_C = 60; equal scores rank
        in input order), and that fused order is blended with the input (pool) order exactly as rerank.stage1 blends
        its single score order (rerank.stage1.blend_order over the fused values: 1/(c + fused rank) + 1/(c + input
        rank), ties in input order). options.rerank_mode "score" takes the fused order alone, as stage 1 does;
     c. protection: pass 1's stage 1 top --protect (5) are always among the --stage2-k (default options.stage2_k, 20)
        tables handed to stage 2 (protect_keys: a missing one takes the last unprotected slot of the top k, from pass
        1's docs when the new pool lost it; the bumped table moves right after the top k);
     d. stage 2: the selector the L1 runner builds for graph:<STAGE2_PRESETS[preset]> (graph:staged-gpt-condyears:
        STAGE2_MODEL / STAGE2_BASE_URL / STAGE2_API_KEY through evals.config.stage2_llm) with the prompt
        stage2-hcx-v5-rw.md and the user message line `검색용 표현: <rewrite>` (rerank.hcx.HcxSelector prompt / hint);
     e. gate: the graph's rule on the top 5 (gate.node.decide_route): refuse when there is no doc, or when stage 2 ran
        without error and picked nothing.
     Otherwise path A (fallback_reason = the rewrite status none | rejected | error | empty, or tau / missing): with
     --reuse-baseline (on by default when --baseline-run is given) "A-baseline" takes that run's row for the qid (keys,
     stage 2 fields, its latency_ms as search_ms; no LLM call); without it, or when the baseline has no row for the
     qid, "A-fallback" runs the graph's own rerank2 (v4, no hint, options.stage2_k) on pass 1's order, then the gate.
--rewrite off: path "A" (reason "off") for every question, run live (pass 1, rerank2 v4, gate): the baseline graph in
this script's row format. --baseline-run is then only compared with.

Rows (data/lab/rewrite/<build>/rw_eval/<stage>/results.jsonl, the L1 row fields report.py grades plus): qid, split,
answerable, path, fallback_reason, rewrite_status, variant, rewrite_preset, route, no_answer_pred (route refuse),
keys (top 10, as evals.run_eval --k), rank (the evaluator's first_rank over keys), hit@1 / hit@5 / hit@10, rr (None
for a no-answer item), the stage 2 fields, stage1_top_score / stage1_margin (pass 1), protected_added, rewrite_ms (the
rewrite step: wall_seconds of the rewrite row, else its call seconds; None when no rewrite was made), search_ms
(everything else measured here: pass 1, rewrite lists, rerank1, stage 2, gate; for A-baseline the baseline's
latency_ms), latency_ms = search_ms + rewrite_ms (rewrite_ms None adds 0), config_id, rewrite_hash. No question and
no rewrite text. config_id hashes the configuration and the search identity (search_identity: collection,
checkpoints, question index build, text format, filter, pool, stage 1 n / mode, stage 2 thinking / model / server;
plus rewrite mode, tau, rewrite prompt file and sha, rewriter, rewrites source, preset, variant, build, weight,
protect, k, prompt, baseline run and its reuse), through stable_config: no server URL (a restarted tunnel is the
same run; model names stay) and checkpoints by file name. A resumed run and the summary also take rows of the legacy
id (config_id of the raw configuration, as rows were written before; config_ids), so the rows of a run started
before this change are kept when the settings are the same. A rerun keeps a qid's last row of these ids only when
it has no stage2_error and, unless its reason is off or tau (no rewrite involved), was made from the same rewrite
(rewrite_hash of the rewrite row's status and text) and is not a path A row whose reason is error or missing
(is_done); everything else is asked again, and a search exception writes nothing. The summary reads the last row per
qid of these ids made from the current rewrite. The console shows a counter only (and, for a dev item, the text of a
search error). A baseline that names its stage 2 model (L1 manifest stage2_llm.model, or an rw_eval summary) other
than this run's is warned about. config_id has no split: a
--split all run of the same configuration and --stage resumes the rows of an earlier --split dev run.

summary.json and the printed report hold aggregates only, per split (dev, test and all when --split all):
grade_rows / aggregate of report.py (Hit@1 / Hit@5 / MRR on answerable items, no-answer P/R/F1 of the graph rule,
errors, latency) by all / style (meta file) / qtype / path / rewrite_status, latency p50 / p95 of latency_ms,
search_ms and rewrite_ms; with --baseline-run (an evals.run_eval L1 run folder, e.g. the B1 run, or a --rewrite off
stage folder), the baseline's aggregates over the same qids and the paired comparison (all items and path B items):
Hit@1 up / down, rank better / worse, MRR change, no-answer items answered wrongly, answerable items refused; and, on
dev only, the plan 07 Q3 dev check over all paired items (dev_check): net Hit@1 drop <= 1 and net rise of wrongly
answered no-answer items <= 1, passed only when every dev item was run and paired, with no stage 2 error and no
search failure. The conditional choice of tau and the test acceptance are trigger_select.py's.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "vendor" / "phase1" / "src", ROOT / "vendor" / "phase1", ROOT / "vendor" / "phase2" / "src",
           ROOT / "src", ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
os.environ.setdefault("KOSIS_WORKSPACE", str(ROOT))

from kos192_lib import (GOLDEN, LAB, META, REWRITE_BUILDS, STAGE2_PRESETS, VARIANTS, add_preset_arg,  # noqa: E402
                        add_rewrite_prompt_arg, add_search_args, apply_search_settings, ckpt_name, fmt, fuse_rewrite,
                        gold_rank, json_safe, load_golden, load_meta, load_run, read_jsonl, require_file,
                        rewrite_prompt_file, rewrite_search, route_no_answer, row_preset, same_search, search_rewrite,
                        stage1_pass, stage1_signals, stage2_resources, table_names, triggered, utf8_stdout,
                        without_stage2, write_json)

PRESET = "staged-condyears"  # plan 07: the B1 baseline's search (graph:staged-gpt-condyears up to stage 1)
REWRITE_PROMPT = "v2"  # 11/17: new experiments use rewrite-v2.md (--build then defaults to REWRITE_BUILDS["v2"])
VARIANT = "R2"  # H1: the rewriter saw the top 10 stage 1 table names
RW_WEIGHT = 2.0  # H1: RRF weight of each rewrite list (pass 1's lists keep 1)
PROTECT = 5  # plan §4.5 / G3: pass 1's stage 1 top 5 always reach stage 2
TOP_K = 10  # keys kept per row: evals.run_eval's default --k, so the rows grade like the baseline L1 rows
GATE_K = 5  # gate sees docs[:k]: graph.search.build_graph's default k, as in the L1 runs
HITS = (1, 5, 10)
DEV_MAX_HIT1_DROP = 1  # plan 07 Q3 dev check: net Hit@1 drop of at most 1 item
DEV_MAX_WRONG_ANSWER_RISE = 1  # and at most 1 more no-answer item answered
STAGE2_ROW_FIELDS = ("stage2_picked_n", "stage2_reason", "stage2_error", "stage2_calls", "stage2_input_tokens",
                     "stage2_output_tokens", "stage2_seconds")
DIMENSIONS = ("all", "style", "qtype", "path", "rewrite_status")
NOT_DONE_REASONS = ("error", "missing")  # a path A row for these waits for a rewrite: never kept on resume
NO_REWRITE_REASONS = ("off", "tau")  # path A rows that involve no rewrite: their rewrite_hash is not compared
MODES = ("on", "off")  # --rewrite
SPLITS = ("dev", "test", "all")
LATENCIES = ("latency_ms", "search_ms", "rewrite_ms")
URL_KEYS = ("base_url", "stage2_base_url")  # left out of the row id (stable_config): a tunnel restart is the same run
CKPT_KEYS = ("embed_ckpt", "rerank_ckpt")  # compared by file name (kos192_lib.ckpt_name)


def check_split(split: str, allow_test: bool = False) -> None:
    """dev, or test / all with allow_test (the one final run, plan 07 Q5). SystemExit otherwise, before anything is
    loaded."""
    if split == "dev" or (split in ("test", "all") and allow_test):
        return
    if split in ("test", "all"):
        raise SystemExit(f"--split {split} runs the test split, which runs once at the end (plan 07 Q5): pass "
                         "--allow-test for that run")
    raise SystemExit(f"--split must be one of {', '.join(SPLITS)}, not {split!r}")


def splits_of(split: str) -> tuple[str, ...]:
    """The summary's splits for a --split value: dev, test and all for "all". Pure."""
    return ("dev", "test", "all") if split == "all" else (split,)


# --- path B steps (pure) --------------------------------------------------------------------------------------------

def choose_path(rewrite_row: dict | None) -> tuple[str, str | None]:
    """("B", None) when the rewrite row is "used" with a non-empty rewrite; ("A-fallback", reason) otherwise, reason =
    the row's status (none | rejected | error), "missing" without a row, "empty" for a used row without text. Pure."""
    if rewrite_row is None:
        return "A-fallback", "missing"
    status = rewrite_row.get("status")
    if status == "used":
        return ("B", None) if (rewrite_row.get("rewrite") or "").strip() else ("A-fallback", "empty")
    return "A-fallback", status or "missing"


def score_ranks(scores: list[float]) -> list[int]:
    """1-based rank of each position by score, highest first; equal scores rank in input order (as stage 1). Pure."""
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    ranks = [0] * len(scores)
    for r, i in enumerate(order, 1):
        ranks[i] = r
    return ranks


def fused_stage1_order(scores_q: list[float], scores_rw: list[float], mode: str = "blend",
                       c: int | None = None) -> list[int]:
    """Input positions in path B's stage 1 order: the two score orders fused by RRF, then blended with the input
    order as rerank.stage1 does.

    In: scores_q / scores_rw = one cross-encoder score per input position for the original question / the rewrite;
        mode = "blend" | "score" (options.rerank_mode); c = RRF constant (None: kosis_rag.shared.constants.RRF_C).
    Out: input positions (0-based). fused(i) = 1/(c + rank_q(i)) + 1/(c + rank_rw(i)); "score" orders by fused alone
        (ties in input order), "blend" passes the fused values to rerank.stage1.blend_order (1/(c + fused rank) +
        1/(c + input rank)). With scores_rw == scores_q it is exactly stage 1's own order.
    Side effects: none. ValueError for an unknown mode or lists of different length.
    """
    from kosis_rag.rerank.stage1 import blend_order
    from kosis_rag.shared.constants import RRF_C

    if mode not in ("blend", "score"):
        raise ValueError(f"unknown stage 1 mode {mode!r}; known: blend, score")
    if len(scores_q) != len(scores_rw):
        raise ValueError("one score per position for both questions")
    c = RRF_C if c is None else c
    fused = [1 / (c + a) + 1 / (c + b) for a, b in zip(score_ranks(scores_q), score_ranks(scores_rw))]
    if mode == "score":
        return sorted(range(len(fused)), key=lambda i: -fused[i])
    return blend_order(fused, c)


def rerank1_fused(question: str, rewrite: str, docs: list[Any], resources: Any) -> list[Any]:
    """Path B's rerank1: re-order the top options.rerank_n of the fused pool by fused_stage1_order; the tail follows.

    In: question = the original question; rewrite = the used rewrite; docs = the fused pool (metadata["key"]);
        resources = stage1_score (the cross-encoder Score) and options.rerank_n / rerank_mode.
    Out: the re-ordered head, then the rest. Each head doc gets input_rank, stage1_score (original question, so
        stage1_signals reads as on pass 1), stage1_rank, stage1_score_rw, stage1_rank_rw and stage1_seconds (both
        scorings). Side effects: two cross-encoder calls.
    """
    from langchain_core.documents import Document

    s = resources.options
    head, tail = docs[: s.rerank_n], docs[s.rerank_n:]
    if not head:
        return docs
    keys = [d.metadata["key"] for d in head]
    started = time.perf_counter()
    sc_q, sc_rw = resources.stage1_score(question, keys), resources.stage1_score(rewrite, keys)
    seconds = time.perf_counter() - started
    order = fused_stage1_order(sc_q, sc_rw, s.rerank_mode)
    rank_q, rank_rw = score_ranks(sc_q), score_ranks(sc_rw)
    return [Document(page_content=head[i].page_content, id=head[i].id, metadata={
        **head[i].metadata, "input_rank": i + 1, "stage1_score": sc_q[i], "stage1_rank": rank_q[i],
        "stage1_score_rw": sc_rw[i], "stage1_rank_rw": rank_rw[i], "stage1_seconds": seconds}) for i in order] + tail


def protect_keys(order: list[str], protected: list[str], k: int) -> tuple[list[str], list[str]]:
    """`order` with every protected key inside the first k. Pure.

    In: order = keys in path B's order; protected = pass 1's stage 1 top keys (at most k of them are kept, in order);
        k = what stage 2 sees. Out: (keys, added): a protected key outside the top k (or not in `order` at all) is
        added at the end of the top k in protected order, taking the slots of the last unprotected keys of the top k,
        which move right after it (before the old tail); added = the protected keys that were moved in.
    """
    protected = list(dict.fromkeys(protected))[:k]
    head = list(order[:k])
    added = [p for p in protected if p not in head]
    if not added:
        return list(order), []
    free = max(0, len(head) + len(added) - k)
    bumped: list[str] = []
    for key in reversed(list(head)):
        if free == 0:
            break
        if key not in protected:
            head.remove(key)
            bumped.insert(0, key)
            free -= 1
    return head + added + bumped + [x for x in order[k:] if x not in added], added


def protect_docs(ranked: list[Any], pass1: list[Any], n: int, k: int) -> tuple[list[Any], int]:
    """protect_keys over documents: pass 1's top n keys kept in the first k of `ranked`. A protected table the new
    pool lost comes back as pass 1's document. Out: (docs, number of protected tables moved in). Pure."""
    keys, added = protect_keys([d.metadata["key"] for d in ranked], [d.metadata["key"] for d in pass1[:n]], k)
    by_key = {d.metadata["key"]: d for d in pass1}
    by_key.update({d.metadata["key"]: d for d in ranked})  # the new pool's document when it holds the table
    return [by_key[key] for key in keys], len(added)


def rank_fields(item: Any, keys: list[str]) -> dict:
    """rank (the evaluator's first_rank over keys), hit@1 / @5 / @10 and rr of an answerable item; all None for a
    no-answer item."""
    if not item.answerable:
        return {"rank": None, **{f"hit@{k}": None for k in HITS}, "rr": None}
    rank = gold_rank(item, keys)
    return {"rank": rank, **{f"hit@{k}": int(rank is not None and rank <= k) for k in HITS},
            "rr": 1 / rank if rank else 0.0}


def total_latency(search_ms: float, rewrite_ms: float | None) -> float:
    """The item's estimated service time: the search measured here plus the rewrite call (None adds nothing)."""
    return search_ms + (rewrite_ms or 0.0)


# --- resume ---------------------------------------------------------------------------------------------------------

def search_identity(resources: Any) -> dict:
    """What the search and stage 2 ran with, for config_id: collection, embedder / re-ranker checkpoints, question index
    build, text format, condition filter and scope, pool, stage 1 n / mode, question-index weight / BM25, stage 2 name
    and k of the graph, and the stage 2 LLM's thinking / model / base URL. Pure (reads resources.spec / options)."""
    spec, o, llm = resources.spec, resources.options, resources.stage2_llm
    return {"collection": spec.chroma.collection, "embed_ckpt": spec.embed_ckpt, "rerank_ckpt": spec.rerank_ckpt,
            "hq_build": spec.hq_build, "text_format": resources.text_format, "cond_filter": o.cond_filter,
            "cond_scope": o.cond_scope, "pool": o.pool, "rerank_n": o.rerank_n, "rerank_mode": o.rerank_mode,
            "hq_weight": o.hq_weight, "hq_bm25": o.hq_bm25, "stage2": o.stage2, "graph_stage2_k": o.stage2_k,
            "stage2_thinking": getattr(llm, "thinking", None), "stage2_model": getattr(llm, "model", None),
            "stage2_base_url": getattr(llm, "base_url", None)}


def config_id(config: dict) -> str:
    """12 hex digits of the sha256 of the configuration (sorted JSON, paths as text): rows of another configuration
    are ignored. Applied to the raw configuration it is the legacy id (rows written before stable_config); main
    writes config_id(stable_config(config))."""
    text = json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def stable_config(config: dict) -> dict:
    """The configuration the row id hashes: `config` without any server URL (URL_KEYS at any depth: a restarted
    tunnel or another port is the same run; the model names stay) and with checkpoint paths (CKPT_KEYS) reduced to
    kos192_lib.ckpt_name. Pure: `config` is not changed."""
    def walk(value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {k: walk(v, k) for k, v in value.items() if k not in URL_KEYS}
        return ckpt_name(value) if key in CKPT_KEYS else value

    return walk(config)


def config_ids(config: dict) -> tuple[str, str]:
    """(id, legacy id) of a configuration: config_id(stable_config(config)), which new rows carry, and
    config_id(config), the formula of rows written before (a resumed run and the summary accept both). Pure."""
    return config_id(stable_config(config)), config_id(config)


def rewrite_hash(rewrite_row: dict | None) -> str:
    """12 hex digits of the sha256 of the rewrite row's (status, rewrite text); no row hashes ("missing", None). A
    result row made from another rewrite of its qid is not done."""
    status, text = (rewrite_row.get("status"), rewrite_row.get("rewrite")) if rewrite_row else ("missing", None)
    return hashlib.sha256(json.dumps([status, text], ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def current_rows(rows: list[dict], cid: str | Iterable[str]) -> dict[str, dict]:
    """qid -> the last row of this configuration (first-seen qid order); cid = one id or several (config_ids: the
    id and the legacy id of the same configuration). Pure."""
    ids = {cid} if isinstance(cid, str) else set(cid)
    last: dict[str, dict] = {}
    for r in rows:
        if r.get("config_id") in ids:
            last[r["qid"]] = r
    return last


def is_done(row: dict, rewrite_row: dict | None) -> bool:
    """A result row a resumed run keeps: no stage 2 error and, unless its reason is off or tau (no rewrite involved),
    made from this rewrite row (rewrite_hash) and not a path A row whose reason is error or missing (it waits for a
    rewrite). Pure."""
    if row.get("stage2_error"):
        return False
    if row.get("fallback_reason") in NO_REWRITE_REASONS:
        return True
    if row.get("rewrite_hash") != rewrite_hash(rewrite_row):
        return False
    return not (str(row.get("path", "")).startswith("A") and row.get("fallback_reason") in NOT_DONE_REASONS)


def done_qids(rows: list[dict], cid: str | Iterable[str], rewrites: dict[str, dict]) -> set[str]:
    """qids a resumed run skips: their last row of this configuration is_done for the current rewrite row
    (rewrites = qid -> rewrites.jsonl row of the variant). Pure."""
    return {q for q, r in current_rows(rows, cid).items() if is_done(r, rewrites.get(q))}


def fresh_rows(rows: list[dict], cid: str | Iterable[str], rewrites: dict[str, dict]) -> list[dict]:
    """The rows the summary reads: the last row per qid of this configuration made from the current rewrite row (a
    row of an older rewrite is left out until it is run again). Pure."""
    return [r for q, r in current_rows(rows, cid).items()
            if r.get("fallback_reason") in NO_REWRITE_REASONS or r.get("rewrite_hash") == rewrite_hash(rewrites.get(q))]


def check_rewrites(rewrites: dict[str, dict], items: list[Any], *, variant: str, build: str,
                   allow_missing: bool) -> int:
    """--rewrites-from only (pre-generated rows): SystemExit when that build has no row of the variant, or (unless
    allow_missing) when a question of the run has no row; out: how many have none. Counts per split, no qid. Pure."""
    if not rewrites:
        raise SystemExit(f"no {variant} rows of this rewrite prompt in data/lab/rewrite/{build}/rewrites.jsonl: make "
                         f"them with rw_eval.py --rewrite on (inline) or gen_rewrites.py --build {build} --variants "
                         f"{variant} --rewrite-prompt <the same prompt>")
    missing = Counter(i.split for i in items if i.qid not in rewrites)
    total = Counter(i.split for i in items)
    if missing and not allow_missing:
        counts = ", ".join(f"{s} {missing[s]}/{total[s]}" for s in sorted(missing))
        raise SystemExit(f"questions without a {variant} row in data/lab/rewrite/{build}/rewrites.jsonl: {counts}. "
                         "Drop --rewrites-from to make them inline (rw_eval.py --rewrite on caches them), finish "
                         f"gen_rewrites.py --build {build} --variants {variant} [--split test --allow-test] (it "
                         "resumes), or pass --allow-missing to run them as path A")
    return sum(missing.values())


# --- one item (search, models, LLM) ---------------------------------------------------------------------------------

def rewrite_ms_of(rewrite_row: dict | None) -> float | None:
    """The rewrite step's time in ms (any status): the row's wall_seconds (measured inline: call, parse, check), else
    its call seconds (a gen_rewrites row); None without a row or a time. Pure."""
    if not rewrite_row:
        return None
    seconds = rewrite_row.get("wall_seconds", rewrite_row.get("seconds"))
    return seconds * 1000 if seconds is not None else None


def result_row(item: Any, rewrite_row: dict | None, *, path: str, reason: str | None, variant: str, refuse: bool,
               keys: list[str], stage2: dict, top: float | None, margin: float | None, protected_added: int,
               search_ms: float | None, status: str | None = None) -> dict:
    """One results.jsonl row (module docstring), config_id not set: keys cut to TOP_K and graded (rank_fields),
    rewrite_ms from the rewrite row, latency_ms = search_ms + rewrite_ms (None when search_ms is None); status =
    rewrite_status when no rewrite row tells it (off, skipped; default: the row's status, or missing). Pure."""
    keys = list(keys)[:TOP_K]
    rewrite_ms = rewrite_ms_of(rewrite_row)
    status = status or (rewrite_row.get("status") if rewrite_row else "missing")
    return {"qid": item.qid, "split": item.split, "answerable": item.answerable, "path": path,
            "fallback_reason": reason, "rewrite_status": status,
            "rewrite_hash": rewrite_hash(rewrite_row), "variant": variant,
            "rewrite_preset": row_preset(rewrite_row) if rewrite_row else None,
            "route": "refuse" if refuse else "found", "no_answer_pred": refuse, "keys": keys, **rank_fields(item, keys),
            **{f: stage2.get(f) for f in STAGE2_ROW_FIELDS}, "stage1_top_score": top, "stage1_margin": margin,
            "protected_added": protected_added, "rewrite_ms": rewrite_ms, "search_ms": search_ms,
            "latency_ms": total_latency(search_ms, rewrite_ms) if search_ms is not None else None}


def from_baseline(item: Any, rewrite_row: dict | None, base_row: dict, reason: str | None, variant: str,
                  status: str | None = None) -> dict:
    """Path "A-baseline": a path A item read from the baseline run's row for its qid (an L1 row: keys, stage 2 fields,
    stage1_top_score / stage1_margin, latency_ms), so it is exactly the baseline graph's result and no LLM is called.
    The route is the graph rule over that row (kos192_lib.route_no_answer); search_ms = its latency_ms. Pure."""
    return result_row(item, rewrite_row, path="A-baseline", reason=reason, variant=variant,
                      refuse=route_no_answer(base_row), keys=base_row.get("keys") or [], stage2=base_row,
                      top=base_row.get("stage1_top_score"), margin=base_row.get("stage1_margin"), protected_added=0,
                      search_ms=base_row.get("latency_ms"), status=status)


@dataclasses.dataclass
class Setup:
    """What every question of one run shares (main builds it; tests build a small one).

    graph = the stage 1 graph (build_graph over without_stage2(resources)); resources = stage2_resources(preset);
    select_rw = the v5-rw selector (HcxSelector, prompt PROMPT_FILE_RW); mode = on | off; tau = --rewrite-tau (None:
    always); k = stage 2 input on path B; baseline = qid -> baseline row reused for path A ({}: path A runs live);
    rewrites = qid -> rewrite row (the cache, or --rewrites-from; generate adds to it); generate = (item, pass 1
    state) -> a new rewrite row, cached by the caller's function (None: read rewrites only).
    """

    graph: Any
    resources: Any
    select_rw: Any = None
    mode: str = "on"
    tau: float | None = None
    variant: str = VARIANT
    rw_weight: float = RW_WEIGHT
    protect: int = PROTECT
    k: int = 20
    baseline: dict[str, dict] = dataclasses.field(default_factory=dict)
    rewrites: dict[str, dict] = dataclasses.field(default_factory=dict)
    generate: Callable[[Any, Any], dict] | None = None


def cached_rewrites(rows: list[dict], *, sha: str, model: str, preset: str, variant: str,
                    search: dict | None = None) -> dict[str, dict]:
    """qid -> the cached rewrite row inline generation reuses: among the rows of the same search (same_search over
    kos192_lib.rewrite_search: collection, checkpoints; a row without "search", or without a field, matches), the last
    row per qid of the variant made with this prompt sha, model and preset (gen_rewrites.current_rows) that is not an
    error (an error is asked again). A row of another prompt is never reused. Pure."""
    from gen_rewrites import current_rows as made_with

    rows = [r for r in rows if same_search(r.get("search"), search)]
    return {r["qid"]: r for r in made_with(rows, sha, model, preset)
            if r["variant"] == variant and r["status"] != "error"}


def source_rewrites(rows: list[dict], *, sha: str, variant: str) -> dict[str, dict]:
    """--rewrites-from: qid -> the last row of the variant made with this prompt sha (any model, preset or status;
    a row without prompt_sha is left out). Pure."""
    return {r["qid"]: r for r in rows if r["variant"] == variant and r.get("prompt_sha") == sha}


def make_generator(client: Any, *, sha: str, model: str, preset: str, variant: str, resources: Any,
                   cache: Path, prompt: str | None = None, search: dict | None = None) -> Callable[[Any, Any], dict]:
    """The inline rewriter: (item, pass 1 state) -> a rewrites.jsonl row, made with gen_rewrites' own code
    (rewrite_checked with `prompt`, the prompt file whose sha is `sha` (None: rewrite_question's default, v1), over pass
    1's parsed query; R2 = the names of pass 1's top MAX_TABLE_NAMES tables) and appended to `cache` at once
    (gen_rewrites.rewrite_row with the prompt file, search = kos192_lib.rewrite_search, wall_seconds = the measured
    rewrite step).
    Side effects when called: one LLM call, a line appended to `cache`."""
    from gen_rewrites import rewrite_checked, rewrite_row
    from kosis_rag.rewrite.constants import MAX_TABLE_NAMES

    def generate(item: Any, state: Any) -> dict:
        keys = [d.metadata["key"] for d in state.docs[:MAX_TABLE_NAMES]]
        names = table_names(resources, keys) if variant == "R2" else []
        started = time.perf_counter()
        out = rewrite_checked(item.question, names, client, state.query, resources, prompt=prompt)
        wall = time.perf_counter() - started
        top, margin = stage1_signals(state)
        row = rewrite_row(item, variant, out, top=top, margin=margin, sha=sha, model=model, preset=preset,
                          wall_seconds=wall, prompt=prompt, search=search)
        cache.parent.mkdir(parents=True, exist_ok=True)
        with cache.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    return generate


def run_item(item: Any, s: Setup) -> dict:
    """One question under the run's setup; the module docstring's steps.

    off: path "A" (reason off), live. on: a cached / given rewrite row that already says path A, with a baseline row
    to reuse and no tau, is answered from the baseline without any search; otherwise pass 1, then tau (path A, reason
    tau, when the stage 1 top score is not below it), the rewrite (inline when there is no row and s.generate is
    set: added to s.rewrites), and path B or path A (A-baseline when the baseline has the qid, else A-fallback live).
    Out: the result row (result_row), config_id not set; search_ms excludes the inline rewrite step (rewrite_ms).
    Side effects: Chroma, embedder, cross-encoder, the rewrite and stage 2 LLM calls as needed; exceptions of the
        search propagate (the caller writes no row).
    """
    from kosis_rag.gate import node as gate_step
    from kosis_rag.gate.node import GateInput
    from kosis_rag.rerank import node as rerank_step
    from kosis_rag.rerank.schemas import Stage2Input
    from kosis_rag.rerank.stage2 import rerank_stage2

    base_row = s.baseline.get(item.qid)
    rewrite_row = s.rewrites.get(item.qid) if s.mode == "on" else None
    if s.mode == "on" and s.tau is None and base_row is not None and (rewrite_row is not None or s.generate is None):
        path, reason = choose_path(rewrite_row)
        if path != "B":  # known to be path A: the baseline's own row, no search
            return from_baseline(item, rewrite_row, base_row, reason, s.variant)
    started = time.perf_counter()
    state = stage1_pass(s.graph, item.question)  # 1. pass 1 (parse -> condition -> lists -> fuse -> rerank1)
    pass1 = state.docs
    top, margin = stage1_signals(state)
    rewrite_s, added, status = 0.0, 0, None
    if s.mode == "off":
        path, reason, status = "A", "off", "off"
    elif s.tau is not None and not triggered(top, s.tau):
        path, reason, status, rewrite_row = "A-fallback", "tau", "skipped", None
    else:
        if rewrite_row is None and s.generate is not None:  # 2. the rewrite, inline
            t0 = time.perf_counter()
            rewrite_row = s.generate(item, state)
            rewrite_s = time.perf_counter() - t0
            s.rewrites[item.qid] = rewrite_row
        path, reason = choose_path(rewrite_row)
    if path != "B" and path != "A" and base_row is not None:  # path A of the on mode: the baseline's row
        return from_baseline(item, rewrite_row, base_row, reason, s.variant, status)
    if path == "B":  # 3a-3d
        rewrite = rewrite_row["rewrite"]
        pool = fuse_rewrite(state.runs, search_rewrite(rewrite, state.table_filter, s.resources), s.rw_weight,
                            s.resources)
        handed, added = protect_docs(rerank1_fused(item.question, rewrite, pool, s.resources), pass1, s.protect, s.k)
        docs = rerank_stage2(item.question, handed, functools.partial(s.select_rw, hint=rewrite), k_in=s.k,
                             name=s.resources.stage2_name)
    else:  # the graph's own rerank2 (v4, options.stage2_k) on pass 1's order
        docs = rerank_step.rerank2(Stage2Input(question=item.question, docs=pass1), s.resources).docs
    route = gate_step.decide_route(GateInput(docs=docs[:GATE_K]), s.resources).route  # 3e
    search_ms = (time.perf_counter() - started - rewrite_s) * 1000
    return result_row(item, rewrite_row, path=path, reason=reason, variant=s.variant, refuse=route == "refuse",
                      keys=[d.metadata["key"] for d in docs[:TOP_K]], stage2=docs[0].metadata if docs else {},
                      top=top, margin=margin, protected_added=added, search_ms=search_ms, status=status)


# --- summary (pure) -------------------------------------------------------------------------------------------------

def _report():
    """scripts/kos192/report.py (grade_rows, aggregate, render): this folder is on sys.path."""
    import report

    return report


def paired_compare(rw: list[dict], base: list[dict]) -> dict:
    """Item-paired comparison of graded records (report.grade_rows) of this run and the baseline, over the qids in
    both. Pure.

    Out: n, n_answerable, n_no_answer; hit1 {rw, base, up (hit here, not in the baseline), down}; rank {better, worse}
        (a gold table outside the top 10 ranks last); mrr_delta (mean rr here minus baseline, answerable; None
        without any); wrong_answer {rw, base, fixed (baseline answered, refused here), broken} over no-answer items;
        answerable_refused {rw, base}.
    """
    by_qid = {g["qid"]: g for g in base}
    pairs = [(g, by_qid[g["qid"]]) for g in rw if g["qid"] in by_qid]
    ans = [(a, b) for a, b in pairs if a["answerable"]]
    na = [(a, b) for a, b in pairs if not a["answerable"]]
    rk = lambda r: r if r is not None else math.inf  # noqa: E731
    hit_rw, hit_base = sum(a["hit@1"] for a, _ in ans), sum(b["hit@1"] for _, b in ans)
    wrong_rw = sum(1 for a, _ in na if not a["no_answer_pred"])
    wrong_base = sum(1 for _, b in na if not b["no_answer_pred"])
    return {"n": len(pairs), "n_answerable": len(ans), "n_no_answer": len(na),
            "hit1": {"rw": hit_rw, "base": hit_base, "up": sum(1 for a, b in ans if a["hit@1"] and not b["hit@1"]),
                     "down": sum(1 for a, b in ans if b["hit@1"] and not a["hit@1"])},
            "rank": {"better": sum(1 for a, b in ans if rk(a["rank"]) < rk(b["rank"])),
                     "worse": sum(1 for a, b in ans if rk(a["rank"]) > rk(b["rank"]))},
            "mrr_delta": sum(a["rr"] - b["rr"] for a, b in ans) / len(ans) if ans else None,
            "wrong_answer": {"rw": wrong_rw, "base": wrong_base,
                             "fixed": sum(1 for a, b in na if a["no_answer_pred"] and not b["no_answer_pred"]),
                             "broken": sum(1 for a, b in na if not a["no_answer_pred"] and b["no_answer_pred"])},
            "answerable_refused": {"rw": sum(1 for a, _ in ans if a["no_answer_pred"]),
                                   "base": sum(1 for _, b in ans if b["no_answer_pred"])}}


def dev_check(paired: dict, *, n_expected: int | None, n_graded: int, stage2_errors: int, search_failed: int) -> dict:
    """The plan 07 Q3 dev check over all paired items. Pure.

    In: paired = paired_compare over all items; n_expected = questions of the split this run covers (None: unknown);
        n_graded = graded rows; stage2_errors = rows with a stage 2 error; search_failed = items whose search raised.
    Out: hit1_net_drop (baseline Hit@1 count - this run's), wrong_answer_net_rise (no-answer items answered here -
        in the baseline), complete (every expected item graded and paired), n_expected, n_paired, stage2_errors,
        search_failed, pass = complete, no stage 2 error, no search failure, drop <= DEV_MAX_HIT1_DROP and rise <=
        DEV_MAX_WRONG_ANSWER_RISE.
    """
    drop = paired["hit1"]["base"] - paired["hit1"]["rw"]
    rise = paired["wrong_answer"]["rw"] - paired["wrong_answer"]["base"]
    complete = n_expected is not None and n_graded == n_expected and paired["n"] == n_expected
    return {"hit1_net_drop": drop, "wrong_answer_net_rise": rise, "complete": complete, "n_expected": n_expected,
            "n_paired": paired["n"], "stage2_errors": stage2_errors, "search_failed": search_failed,
            "pass": (complete and stage2_errors == 0 and search_failed == 0 and drop <= DEV_MAX_HIT1_DROP
                     and rise <= DEV_MAX_WRONG_ANSWER_RISE)}


def latency_stats(rows: list[dict]) -> dict[str, dict]:
    """field -> {n, p50, p95} (ms, evals.ops.percentile as report.py) for latency_ms, search_ms, rewrite_ms over the
    rows that carry the value. Pure."""
    from evals.ops import percentile

    out = {}
    for field in LATENCIES:
        values = [r[field] for r in rows if r.get(field) is not None]
        out[field] = {"n": len(values), "p50": percentile(values, 50) if values else None,
                      "p95": percentile(values, 95) if values else None}
    return out


def summarize(rows: list[dict], golden: dict, meta: dict[str, dict], split: str,
              base_rows: list[dict] | None = None, *, n_expected: int | None = None, search_failed: int = 0) -> dict:
    """Aggregates of this run's rows (one per qid) and, with base_rows (an L1 run's results.jsonl rows), of the
    baseline over the same qids, the paired comparison and the dev check. Every count is over the rows of `split`
    (the graded qids), so a --split all run's dev summary does not count its test rows. Pure: no file is read.

    Out: n_graded, not_in_golden, groups (report.aggregate by DIMENSIONS), latency (latency_stats), paths (path ->
        count), fallback_reasons, stage2_errors, search_failed, protected {items, tables}; with base_rows: baseline
        {n_graded, groups (all, style)}, paired {"all": paired_compare, "path_B": over path B items}, and for split
        "dev" only dev_check (over "all" with n_expected and search_failed).
    """
    report = _report()
    graded, unknown = report.grade_rows(rows, golden, meta, split)
    qids = {g["qid"] for g in graded}
    rows = [r for r in rows if r["qid"] in qids]  # this split's rows only: paths, latency, errors, protection
    path_of = {r["qid"]: r["path"] for r in rows}
    for g in graded:
        g["path"] = path_of[g["qid"]]
    out = {"n_graded": len(graded), "not_in_golden": unknown, "groups": report.aggregate(graded, DIMENSIONS),
           "latency": latency_stats(rows), "paths": dict(sorted(Counter(r["path"] for r in rows).items())),
           "fallback_reasons": dict(sorted(Counter(r["fallback_reason"] for r in rows if r["fallback_reason"]).items())),
           "stage2_errors": sum(1 for r in rows if r.get("stage2_error")), "search_failed": search_failed,
           "protected": {"items": sum(1 for r in rows if r.get("protected_added")),
                         "tables": sum(r.get("protected_added") or 0 for r in rows)}}
    if base_rows is not None:
        base, _ = report.grade_rows([r for r in base_rows if r["qid"] in qids], golden, meta, split)
        out["baseline"] = {"n_graded": len(base), "groups": report.aggregate(base, ("all", "style"))}
        out["paired"] = {"all": paired_compare(graded, base),
                         "path_B": paired_compare([g for g in graded if g["path"] == "B"], base)}
        if split == "dev":  # plan 07 Q3: the dev check is a dev judgement only
            out["dev_check"] = dev_check(out["paired"]["all"], n_expected=n_expected, n_graded=len(graded),
                                         stage2_errors=out["stage2_errors"], search_failed=search_failed)
    return out


def summarize_splits(rows: list[dict], golden: dict, meta: dict[str, dict], split: str,
                     base_rows: list[dict] | None = None, *, n_expected: dict[str, int] | None = None,
                     search_failed: dict[str, int] | None = None) -> dict[str, dict]:
    """split -> summarize() for each split of the run (splits_of: dev, test and all for "all"); n_expected /
    search_failed per split ("all" = their sums when not given). Pure."""
    n_expected, search_failed = n_expected or {}, search_failed or {}
    out = {}
    for s in splits_of(split):
        n = n_expected.get(s, sum(n_expected.values()) if s == "all" and n_expected else None)
        failed = search_failed.get(s, sum(search_failed.values()) if s == "all" else 0)
        out[s] = {**summarize(rows, golden, meta, s, base_rows, n_expected=n, search_failed=failed), "split": s}
    return out


def load_baseline(folder: Path) -> list[dict]:
    """The baseline rows of --baseline-run: an rw_eval stage folder (summary.json with a config_id: its rows of that
    configuration, the last per qid, e.g. a --rewrite off run) or an evals.run_eval L1 run folder (results.jsonl).
    SystemExit (one line) when the folder has no results.jsonl. Side effects: reads the files."""
    summary = folder / "summary.json"
    ids = summary_ids(json.loads(summary.read_text(encoding="utf-8-sig"))) if summary.is_file() else []
    if ids:
        require_file(folder / "results.jsonl", "results.jsonl of the rw_eval stage folder")
        return list(current_rows(read_jsonl(folder / "results.jsonl"), ids).values())
    return load_run(folder)[0]


def summary_ids(summary: dict) -> list[str]:
    """The row ids an rw_eval summary.json stands for: its config_ids (id and legacy id), or its single config_id
    (summaries written before config_ids); [] for an L1 summary. Pure."""
    return list(summary.get("config_ids") or ([summary["config_id"]] if summary.get("config_id") else []))


def baseline_stage2_model(folder: Path) -> str | None:
    """The stage 2 model the baseline ran with, when it says: an L1 manifest's stage2_llm.model (run_eval records it
    since T3-23), or an rw_eval summary's config model. None otherwise. Side effects: reads the files."""
    for name, pick in (("manifest.json", lambda m: (m.get("stage2_llm") or {}).get("model")),
                       ("summary.json", lambda s: (s.get("config") or {}).get("model"))):
        if (folder / name).is_file():
            return pick(json.loads((folder / name).read_text(encoding="utf-8-sig")))
    return None


def demote(markdown: str) -> str:
    """Every heading one level down (# -> ##), so a nested report keeps the page's single H1. Pure."""
    return "\n".join("#" + line if line.startswith("#") else line for line in markdown.split("\n"))


def render(summary: dict, title: str, baseline_title: str | None = None) -> str:
    """The markdown report (aggregates only: no question, no rewrite, no key). One H1 (this run's report.py table),
    then 경로·지연, the baseline's table (headings demoted), the paired comparisons and the plan 07 dev check."""
    report = _report()
    head = {"split": summary["split"], "n_graded": summary["n_graded"], "not_in_golden": summary["not_in_golden"]}
    text = report.render({**head, "run_id": title, "retriever": summary["label"], "groups": summary["groups"]})
    lat = summary["latency"]
    lines = ["", "## 경로·지연", "",
             "- 경로: " + " · ".join(f"{p} {n}" for p, n in summary["paths"].items())
             + (" (fallback 사유: " + ", ".join(f"{k} {v}" for k, v in summary["fallback_reasons"].items()) + ")"
                if summary["fallback_reasons"] else ""),
             f"- 2차 오류 {summary['stage2_errors']} · 검색 실패 {summary['search_failed']} · 보호로 넣은 표: 문항 "
             f"{summary['protected']['items']}, 표 {summary['protected']['tables']}",
             "- 지연(ms) p50 / p95: " + " · ".join(
                 f"{f} {fmt(lat[f]['p50'], 0)} / {fmt(lat[f]['p95'], 0)} (n {lat[f]['n']})" for f in LATENCIES)]
    if "baseline" in summary:
        base = summary["baseline"]
        lines += ["", demote(report.render({**head, "n_graded": base["n_graded"], "run_id": baseline_title or "baseline",
                                            "retriever": "기준선 (같은 qid)", "groups": base["groups"]})).rstrip("\n")]
        for name, p in summary["paired"].items():
            h, w = p["hit1"], p["wrong_answer"]
            lines += ["", f"## 문항 짝 비교 vs 기준선 ({name}, n {p['n']}: 답 있음 {p['n_answerable']}, "
                          f"답 없음 {p['n_no_answer']})", "",
                      f"- Hit@1 {h['base']} → {h['rw']} (오름 {h['up']} / 내림 {h['down']}), 순위 나아짐 "
                      f"{p['rank']['better']} / 나빠짐 {p['rank']['worse']}, MRR 변화 {fmt(p['mrr_delta'])}",
                      f"- 답 없음 잘못 답함 {w['base']} → {w['rw']} (고침 {w['fixed']} / 새로 틀림 {w['broken']}), "
                      f"답 있음 거절 {p['answerable_refused']['base']} → {p['answerable_refused']['rw']}"]
    if "dev_check" in summary:
        d = summary["dev_check"]
        lines += ["", "## dev 판정 (계획 07 Q3, 기준선 대비 전체 문항)", "",
                  f"- 기준: Hit@1 순감 ≤ {DEV_MAX_HIT1_DROP}, 답 없음 잘못 답함 순증 ≤ {DEV_MAX_WRONG_ANSWER_RISE}",
                  f"- 결과: Hit@1 순감 {d['hit1_net_drop']}, 답 없음 잘못 답함 순증 {d['wrong_answer_net_rise']}",
                  f"- 조건: 문항 {d['n_paired']}/{d['n_expected'] if d['n_expected'] is not None else '-'} 짝 완료"
                  f"{'' if d['complete'] else ' (미완료)'}, 2차 오류 {d['stage2_errors']}, 검색 실패 {d['search_failed']}",
                  f"- 판정: {'통과' if d['pass'] else '미달'}"]
    return text + "\n".join(lines) + "\n"


def render_splits(by_split: dict[str, dict], title: str, baseline_title: str | None = None) -> str:
    """render() of one split, or for several one H1 and each split's report under it (headings demoted). Pure."""
    if len(by_split) == 1:
        return render(next(iter(by_split.values())), title, baseline_title)
    parts = [f"# KOS-192 rw_eval — {title} ({' · '.join(by_split)})", ""]
    parts += [demote(render(s, f"{title} [{name}]", baseline_title)) for name, s in by_split.items()]
    return "\n".join(parts)


# --- main -----------------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    pre = argparse.ArgumentParser(add_help=False)  # the split is refused before anything is loaded
    pre.add_argument("--split", default="dev")
    pre.add_argument("--allow-test", action="store_true")
    known = pre.parse_known_args(argv)[0]
    check_split(known.split, known.allow_test)
    parser = argparse.ArgumentParser(description="KOS-192 Q3 / Q5: the rewrite path B (rewrite inline) or the baseline.")
    parser.add_argument("--golden", type=Path, default=GOLDEN, help="retrieval golden file (default: the KOS-192 set)")
    parser.add_argument("--meta", type=Path, default=META, help="meta file: style per qid")
    parser.add_argument("--split", default="dev", help="dev (default), or test / all with --allow-test")
    parser.add_argument("--allow-test", action="store_true", help="allow --split test | all (plan 07 Q5, once)")
    parser.add_argument("--rewrite", choices=MODES, default="on",
                        help="on: rewrite inline and take path B when used (default); off: the baseline graph, live")
    parser.add_argument("--rewrite-tau", type=float,
                        help="on only: rewrite only when pass 1's stage 1 top score < tau (default: always)")
    parser.add_argument("--rewrites-from", metavar="BUILD",
                        help="on only: read pre-made rows from data/lab/rewrite/<BUILD>/rewrites.jsonl, make none")
    parser.add_argument("--allow-missing", action="store_true",
                        help="with --rewrites-from: run questions without a row as path A (default: refuse)")
    parser.add_argument("--build", help="folder under data/lab/rewrite: rewrites.jsonl cache, rw_eval/<stage>/ "
                        f"(default per --rewrite-prompt: {', '.join(f'{p} {b}' for p, b in REWRITE_BUILDS.items())})")
    parser.add_argument("--variant", choices=VARIANTS, default=VARIANT, help=f"rewrite variant (default {VARIANT})")
    parser.add_argument("--stage", default="q3", help="output folder name under <build>/rw_eval (default q3)")
    parser.add_argument("--rw-weight", type=float, default=RW_WEIGHT,
                        help=f"RRF weight of the rewrite lists (default {RW_WEIGHT:g})")
    parser.add_argument("--protect", type=int, default=PROTECT,
                        help=f"pass 1 stage 1 top n always shown to stage 2 (default {PROTECT})")
    parser.add_argument("--stage2-k", type=int, help="tables stage 2 sees on path B (default: options.stage2_k, 20)")
    parser.add_argument("--baseline-run", type=Path,
                        help="an evals.run_eval L1 run folder or a --rewrite off stage folder to compare with")
    parser.add_argument("--reuse-baseline", action=argparse.BooleanOptionalAction, default=None,
                        help="on only: path A items take the baseline row (default: on when --baseline-run is given)")
    parser.add_argument("--rewrite-model", help="rewriter model (default: STAGE2_MODEL, the stage 2 gpt model)")
    parser.add_argument("--rewrite-base-url", help="rewriter server (default: STAGE2_BASE_URL)")
    parser.add_argument("--allow-remote", action="store_true", help="allow a rewriter config without a base URL (OpenAI)")
    parser.add_argument("--limit", type=int, help="only the first N questions of the split (smoke run)")
    add_preset_arg(parser, default=PRESET)
    add_rewrite_prompt_arg(parser, default=REWRITE_PROMPT)
    add_search_args(parser)
    args = parser.parse_args(argv)
    check_split(args.split, args.allow_test)
    args.build = args.build or REWRITE_BUILDS[args.rewrite_prompt]  # each prompt's rows in their own build
    on = args.rewrite == "on"
    if not on and (args.rewrite_tau is not None or args.rewrites_from or args.reuse_baseline):
        raise SystemExit("--rewrite off runs the baseline graph live: --rewrite-tau, --rewrites-from and "
                         "--reuse-baseline belong to --rewrite on")
    if args.reuse_baseline and not args.baseline_run:
        raise SystemExit("--reuse-baseline needs --baseline-run")
    reuse = on and bool(args.baseline_run) and args.reuse_baseline is not False

    base_rows = load_baseline(args.baseline_run) if args.baseline_run else None  # SystemExit early when missing
    golden = load_golden(args.golden)
    items = [i for i in golden.values() if i.split in splits_of(args.split)]
    items = items[: args.limit] if args.limit else items
    cache = LAB / args.build / "rewrites.jsonl"
    rewrites: dict[str, dict] = {}
    n_missing = 0
    prompt_file = sha = None
    if on:  # the rewrite prompt: its sha keys the cache and --rewrites-from rows
        from gen_rewrites import prompt_sha
        from kosis_rag.rewrite.node import system_prompt as rewrite_prompt

        prompt_file = rewrite_prompt_file(args.rewrite_prompt)
        sha = prompt_sha(rewrite_prompt(prompt_file))
    if on and args.rewrites_from:  # pre-made rows of this prompt: the last per qid of the variant (the newest wins)
        source = LAB / args.rewrites_from / "rewrites.jsonl"
        require_file(source, f"rewrites file of --rewrites-from {args.rewrites_from}")
        rewrites = source_rewrites(read_jsonl(source), sha=sha, variant=args.variant)
        n_missing = check_rewrites(rewrites, items, variant=args.variant, build=args.rewrites_from,
                                   allow_missing=args.allow_missing)
        if n_missing:
            print(f"note: {n_missing} questions have no {args.variant} row: they run as path A (--allow-missing)",
                  file=sys.stderr)

    from kosis_rag.graph.search import build_graph
    from kosis_rag.rerank.constants import PROMPT_FILE_RW
    from kosis_rag.rerank.hcx import HcxSelector, system_prompt

    apply_search_settings(args)
    resources = stage2_resources(args.preset)
    if not isinstance(resources.select, HcxSelector):
        raise SystemExit(f"stage 2 {resources.stage2_name!r} is not the HCX / gpt selector: no v5-rw prompt or hint")
    select_rw = dataclasses.replace(resources.select, prompt=PROMPT_FILE_RW)  # same text_of, config and client
    graph = build_graph(without_stage2(resources))  # pass 1 stops after rerank1
    k = args.stage2_k or resources.options.stage2_k
    llm = resources.stage2_llm
    rewriter, generate = None, None
    if on and not args.rewrites_from:  # inline: gen_rewrites' rewriter, its cache rows reused
        from gen_rewrites import rewriter_config
        from kosis_rag.rewrite.node import rewrite_client

        config_rw = rewriter_config(args)
        rewriter = {"prompt": prompt_file, "prompt_sha": sha, "model": config_rw.model,
                    "base_url": config_rw.base_url}
        cached = read_jsonl(cache)
        search_key = rewrite_search(resources)
        rewrites = cached_rewrites(cached, sha=sha, model=config_rw.model, preset=args.preset, variant=args.variant,
                                   search=search_key)
        other = sum(1 for r in cached if r["variant"] == args.variant and r.get("prompt_sha") != sha)
        if other:
            print(f"note: {other} {args.variant} rows of another rewrite prompt in {cache} are not reused "
                  f"(--rewrite-prompt {args.rewrite_prompt}; prefer a build per prompt)", file=sys.stderr)
        generate = make_generator(rewrite_client(config_rw), sha=sha, model=config_rw.model, preset=args.preset,
                                  variant=args.variant, resources=resources, cache=cache, prompt=prompt_file,
                                  search=search_key)
    mismatch = sum(1 for r in rewrites.values() if row_preset(r) != args.preset)
    if mismatch and args.variant == "R2":
        print(f"warning: {mismatch} {args.variant} rows were made with another preset than {args.preset}",
              file=sys.stderr)
    config = {"rewrite": args.rewrite, "rewrite_tau": args.rewrite_tau if on else None,
              "rewrite_prompt": prompt_file, "rewrite_prompt_sha": sha,
              "rewrites_from": args.rewrites_from if on else None, "rewriter": rewriter,
              "preset": args.preset, "stage2_preset": STAGE2_PRESETS[args.preset], "variant": args.variant,
              "build": args.build, "rw_weight": args.rw_weight, "protect": args.protect, "stage2_k": k,
              "rerank1": f"fused-{resources.options.rerank_mode}", "prompt": PROMPT_FILE_RW,
              "prompt_sha": hashlib.sha256(system_prompt(PROMPT_FILE_RW).encode("utf-8")).hexdigest(),
              "model": llm.model, "base_url": llm.base_url,
              # path A items reuse that run's rows: another baseline makes other rows
              "reuse_baseline": reuse, "baseline_run": args.baseline_run.resolve().name if reuse else None,
              "search": search_identity(resources)}
    cid, legacy = config_ids(config)  # rows carry cid; a resume also takes rows of the legacy id (URLs, raw paths)
    ids = (cid, legacy)
    base_model = baseline_stage2_model(args.baseline_run) if args.baseline_run else None
    if base_model and base_model != llm.model:
        print(f"warning: the baseline ran stage 2 with model {base_model}, this run uses {llm.model}", file=sys.stderr)
    print(f"rewrite {args.rewrite}" + (f" · prompt {prompt_file} sha256 {sha[:12]}" if on else "")
          + (f" · tau {args.rewrite_tau:g}" if on and args.rewrite_tau is not None else "")
          + f" · stage 2: model {llm.model} · base URL {llm.base_url or '(none: OpenAI API)'} · preset {args.preset} · "
          f"{args.variant} · w{args.rw_weight:g} · k {k} · protect {args.protect} · reuse baseline {reuse} · "
          f"split {args.split} ({len(items)}) · config {cid} (legacy {legacy})")
    setup = Setup(graph=graph, resources=resources, select_rw=select_rw, mode=args.rewrite,
                  tau=args.rewrite_tau if on else None, variant=args.variant, rw_weight=args.rw_weight,
                  protect=args.protect, k=k, baseline={r["qid"]: r for r in base_rows} if reuse else {},
                  rewrites=rewrites, generate=generate)

    out_dir = LAB / args.build / "rw_eval" / args.stage
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "results.jsonl"
    done = done_qids(read_jsonl(path), ids, rewrites)
    failed: Counter = Counter()
    with path.open("a", encoding="utf-8") as f:
        for n, item in enumerate(items, 1):
            if item.qid in done:
                continue
            try:
                row = run_item(item, setup)
            except Exception as exc:  # asked again on the next run (nothing written for it)
                failed[item.split] += 1
                # the error text for a dev item; a test item shows the counter and the error type only
                detail = f"{type(exc).__name__}: {exc}" if item.split == "dev" else type(exc).__name__
                print(f"[{n}/{len(items)}] failed: {detail}", file=sys.stderr)
                continue
            f.write(json.dumps(json_safe({**row, "config_id": cid}), ensure_ascii=False) + "\n")
            f.flush()
            # the console shows a counter only (nothing that names or grades the item)
            print(f"[{n}/{len(items)}]")

    qids = {i.qid for i in items}
    rows = [r for r in fresh_rows(read_jsonl(path), ids, setup.rewrites) if r["qid"] in qids]
    meta = load_meta(args.meta)
    if not meta:
        print(f"note: no meta file at {args.meta}: style is '-' for every item", file=sys.stderr)
    by_split = summarize_splits(rows, golden, meta, args.split, base_rows,
                                n_expected=dict(Counter(i.split for i in items)), search_failed=dict(failed))
    label = (f"rw_eval rewrite on ({prompt_file}, {args.preset}, {args.variant}, "
             + (f"tau {args.rewrite_tau:g}" if args.rewrite_tau is not None else "always")
             + f", w{args.rw_weight:g}, k {k}, protect {args.protect}, v5-rw)" if on
             else f"rw_eval rewrite off ({args.preset}, graph v4)")
    for summary in by_split.values():
        summary["label"] = label
    main_split = "all" if args.split == "all" else args.split
    write_json(out_dir / "summary.json", {
        "stage": args.stage, "build": args.build, "config_id": cid, "config_ids": list(ids), "config": config,
        "golden": str(args.golden),
        "rewrite_prompt": prompt_file, "rewrite_prompt_sha": sha,
        "rewrites": str(LAB / args.rewrites_from / "rewrites.jsonl") if on and args.rewrites_from else
        (str(cache) if on else None),
        "baseline_run": str(args.baseline_run) if args.baseline_run else None,
        "rewrite_preset_mismatch": mismatch, "missing_rewrites": n_missing, "n_items": len(items),
        **by_split[main_split], "by_split": by_split})
    print(render_splits(by_split, f"{args.build}/rw_eval/{args.stage}",
                        args.baseline_run.name if args.baseline_run else None), end="")
    if failed:
        print(f"- 검색 실패로 빠진 문항 {sum(failed.values())} (다시 실행하면 그 문항만 다시 함)")
    print(f"-> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
