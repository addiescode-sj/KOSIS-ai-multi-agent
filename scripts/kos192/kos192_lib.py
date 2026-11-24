"""Shared pieces of the KOS-192 (conditional query rewrite) offline scripts in this folder.

    report.py            P0: aggregates of an L1 run by split / style / qtype / rewrite_status (no question, no key)
    trigger_analysis.py  P1: how well the stage 1 top score (and the dense cosine) finds stage 1 misses, no model call
    gen_rewrites.py      P2: R1 / R2 rewrites of the dev split through the local Gemma, checked by code
    recall_compare.py    P3 / Q2: fuse recall with and without the rewrite lists, no LLM
    rw_eval.py           Q3 / Q5: the rewrite path B (rewrite inline, rewrite lists, fused stage 1, top 5 kept, stage 2
                         v5-rw, gate) or the baseline graph (--rewrite off), per split
    trigger_select.py    Q4 / Q5: tau chosen on dev from a rewrite-always run, applied once to test, no model call

Pure helpers (run rows, golden, meta, no-answer by the graph rule, AUC, trigger thresholds, quantiles, bigram
overlap) are importable without loading a model; the search helpers (search arguments, resources, the stage 1 pass)
import evals and kosis_rag only when called. Nothing here reads .env itself: gen_rewrites / recall_compare load it
through evals.run_eval.apply_settings (apply_search_settings), as run_eval does (argument > shell > .env > default).

Presets (--preset, plan 07 Q0): the search a script runs up to stage 1 is an L1 STAGED preset of evals.runners.retrieval
with stage 2 off: "staged" (every condition filters; the P0 baseline and the default, so the v1 results reproduce),
"staged-condyears" (only years / period filter: the B1 baseline graph:staged-gpt-condyears up to stage 1) and
"staged-nocond" (no condition filter). STAGE2_PRESETS names the same search with the OpenAI-compatible stage 2.
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
KOS192 = ROOT / "eval" / "golden" / "kos192"
GOLDEN = KOS192 / "retrieval_test_kos192.jsonl"  # the KOS-192 test set: dev 100, test 500
META = KOS192 / "retrieval_test_kos192.meta.jsonl"  # {"qid", "style" (S0..S6), "target_key", "checked"} per line
LAB = ROOT / "data" / "lab" / "rewrite"  # <build>/rewrites.jsonl, summary.json, recall.jsonl (data/ is gitignored)
TRIGGER_RATES = (0.20, 0.35, 0.50)  # plan §4.2: the tau_rw candidates are the scores that trigger these shares of dev
VARIANTS = ("R1", "R2")  # R1: the question only; R2: and the names of the top stage 1 candidates
REWRITE_LISTS = ("dense", "bm25")  # the lists searched again with the rewrite (plan §4.5)
PRESETS = ("staged", "staged-condyears", "staged-nocond")  # --preset: L1 STAGED presets up to stage 1 (no LLM)
DEFAULT_PRESET = "staged"  # what gen_rewrites / recall_compare ran before --preset (the v1 build)
REWRITE_PROMPTS = ("v1", "v2")  # --rewrite-prompt: kosis_rag.rewrite prompts rewrite-v1.md (PROMPT_FILE), rewrite-v2.md
# rw_eval's default build per rewrite prompt: v1 rows live in v2-condyears (Q1-Q3); v2 (11/17) rows get their own build
REWRITE_BUILDS = {"v1": "v2-condyears", "v2": "v3-condyears-p2"}
# the same search with the OpenAI-compatible stage 2 (STAGE2_MODEL / STAGE2_BASE_URL), as the L1 runs used it
STAGE2_PRESETS = {"staged": "staged-gpt", "staged-condyears": "staged-gpt-condyears", "staged-nocond": "staged-gpt-nocond"}


# --- files ---------------------------------------------------------------------------------------------------------

def utf8_stdout() -> None:
    """Print UTF-8 whatever the console code page is (Windows cp949). Called at the start of every main()."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def require_file(path: Path, what: str) -> None:
    """SystemExit with one line (no traceback) when an input file is missing."""
    if not path.is_file():
        raise SystemExit(f"{what} not found: {path}")


def read_jsonl(path: Path) -> list[dict]:
    """Every non-empty line of a JSONL file as a dict, read as utf-8-sig (a BOM is dropped); [] when the file does
    not exist (an output being resumed). Inputs check require_file first. A last line that does not parse (a write cut
    off by a stopped run) is skipped with a warning on stderr; a bad line before it raises (json.JSONDecodeError)."""
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    rows = [json.loads(line) for line in lines[:-1]]
    if lines:
        try:
            rows.append(json.loads(lines[-1]))
        except json.JSONDecodeError:
            print(f"warning: {path.name}: the last line is cut off (a stopped write): skipped", file=sys.stderr)
    return rows


def load_run(run_dir: Path) -> tuple[list[dict], dict]:
    """(rows, manifest) of an evals.run_eval run folder: results.jsonl (one row per question of the split it showed)
    and manifest.json ({} when missing). SystemExit (one line) when the folder has no results.jsonl."""
    results = run_dir / "results.jsonl"
    require_file(results, "results.jsonl of the run folder (an evals.run_eval run folder is expected)")
    manifest = run_dir / "manifest.json"
    return read_jsonl(results), (json.loads(manifest.read_text(encoding="utf-8-sig")) if manifest.is_file() else {})


def load_golden(path: Path) -> dict[str, Any]:
    """qid -> evals.schemas.RetrievalItem of a retrieval golden file, parsed line by line (utf-8-sig; the split / key
    checks of load_items are run_eval's, not repeated here). SystemExit (one line) when the file is missing."""
    from evals.schemas import RetrievalItem

    require_file(path, "golden file")
    items = [RetrievalItem.model_validate_json(line)
             for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    return {i.qid: i for i in items}


def json_safe(value: Any) -> Any:
    """value with every infinite or NaN float replaced by None (JSON has no Infinity / NaN), at any depth."""
    if isinstance(value, float) and (math.isinf(value) or math.isnan(value)):
        return None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def write_json(path: Path, value: Any) -> None:
    """value as indented UTF-8 JSON without Infinity / NaN (json_safe; allow_nan=False checks it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def load_meta(path: Path) -> dict[str, dict]:
    """qid -> the meta line ({"qid", "style", "target_key", "checked"}); {} when the file does not exist."""
    return {m["qid"]: m for m in read_jsonl(path)}


def in_split(split: str | None, wanted: str) -> bool:
    """A row or item of `split` is shown for --split `wanted` (dev | test | all)."""
    return wanted == "all" or split == wanted


# --- grading (the evaluator's own rules) -----------------------------------------------------------------------------

def route_no_answer(row: dict) -> bool:
    """Predicted no-answer by the graph's gate rule (kosis_rag.gate.node.decide_route): no candidate came back, or stage
    2 ran without error and picked nothing (evals.evaluators.retrieval.llm_no_answer). A search that failed (row
    error, no keys) counts as a refusal."""
    from evals.evaluators.retrieval import llm_no_answer

    return not row.get("keys") or llm_no_answer(row)


def gold_rank(item: Any, keys: list[str]) -> int | None:
    """Rank of the first gold key in `keys` (any slot, any alternative of the OR-group): the evaluator's first_rank."""
    from evals.evaluators.retrieval import first_rank

    return first_rank(item, keys)


# --- statistics -----------------------------------------------------------------------------------------------------

def quantile(values: list[float], q: float) -> float | None:
    """The q-quantile (0..1) by linear interpolation between the sorted values; None for no values."""
    if not values:
        return None
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def auc_low(positives: list[float], negatives: list[float]) -> float | None:
    """AUC of "a low score means positive": P(positive score < negative score) + 0.5 P(tie), from average ranks
    (Mann-Whitney U; tied values share their mean rank). None when either side is empty."""
    if not positives or not negatives:
        return None
    pooled = sorted([(v, 0) for v in positives] + [(v, 1) for v in negatives], key=lambda t: t[0])
    ranks: list[float] = [0.0] * len(pooled)
    i = 0
    while i < len(pooled):
        j = i
        while j + 1 < len(pooled) and pooled[j + 1][0] == pooled[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1  # 1-based mean rank of the tied run
        i = j + 1
    neg_rank_sum = sum(r for r, (_, side) in zip(ranks, pooled) if side == 1)
    n_neg, n_pos = len(negatives), len(positives)
    u_neg = neg_rank_sum - n_neg * (n_neg + 1) / 2  # pairs where the negative scores above the positive (+0.5 ties)
    return u_neg / (n_pos * n_neg)


def trigger_threshold(scores: list[float], rate: float) -> float | None:
    """tau that triggers about `rate` of the scores under the rule `score < tau`: with the scores sorted and k =
    round(rate * n), the midpoint of the k-th and (k+1)-th value (exactly k triggered unless they tie; then fewer).
    k = 0: the lowest score (nothing triggers); k = n: +inf (everything). None for no scores."""
    if not scores:
        return None
    s = sorted(scores)
    k = round(rate * len(s))
    if k <= 0:
        return s[0]
    if k >= len(s):
        return math.inf
    return (s[k - 1] + s[k]) / 2


def triggered(score: float | None, tau: float | None) -> bool:
    """The rewrite rule `score < tau`; a missing score or tau never triggers."""
    return score is not None and tau is not None and score < tau


def mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def fmt(value: float | None, digits: int = 3) -> str:
    """A number for a markdown cell ("-" for None, "inf" for infinity)."""
    if value is None:
        return "-"
    if isinstance(value, float) and math.isinf(value):
        return "inf"
    return f"{value:.{digits}f}"


def share(n: int, total: int) -> str:
    """"n/total (p%)" for a markdown cell, "0/0" when there is nothing to count."""
    return f"{n}/{total} ({n / total:.0%})" if total else "0/0"


# --- question / table name overlap (C5) -----------------------------------------------------------------------------

WORD = re.compile(r"\w+")


def char_bigrams(text: str) -> set[str]:
    """Character bigrams inside each word of the text (lower-cased; no bigram spans a space or punctuation)."""
    return {w[i:i + 2] for w in WORD.findall(text.lower()) for i in range(len(w) - 1)}


def name_overlap(question: str, name: str) -> float:
    """Share of the table name's bigrams that occur in the question (0 for a name without bigrams): how much of the
    table's own wording the question uses. Not symmetric on purpose: dropping chatter from a question alone does not
    raise it."""
    target = char_bigrams(name)
    return len(target & char_bigrams(question)) / len(target) if target else 0.0


def best_overlap(question: str, names: list[str]) -> float | None:
    """The best name_overlap over the gold tables' names (an OR-group: any of them answers); None for no names."""
    return max((name_overlap(question, n) for n in names), default=None)


# --- search (heavy: imported only when called) ------------------------------------------------------------------------

def add_search_args(parser: Any) -> None:
    """The search settings of evals.run_eval (same names: --collection, --embed-ckpt, --rerank-ckpt, ...; argument >
    shell > .env > default) and its --env-file / --no-env-file."""
    from evals.run_eval import SEARCH_ARGS

    group = parser.add_argument_group("settings (argument > shell > .env > default; as evals.run_eval)")
    group.add_argument("--env-file", type=Path, default=None, help="default: <repo>/.env (skipped when KOSIS_DOTENV=off)")
    group.add_argument("--no-env-file", action="store_true", help="do not read any .env")
    for field, (name, what) in SEARCH_ARGS.items():
        group.add_argument("--" + field.replace("_", "-"), type=int if field in ("chroma_port", "answer_k") else str,
                           help=f"{name}: {what}")


def add_preset_arg(parser: Any, default: str = DEFAULT_PRESET) -> None:
    """--preset: the L1 STAGED preset the script searches with up to stage 1 (PRESETS; module docstring)."""
    parser.add_argument("--preset", choices=PRESETS, default=default,
                        help=f"search preset up to stage 1 (default {default}; staged reproduces the v1 build)")


def add_rewrite_prompt_arg(parser: Any, default: str) -> None:
    """--rewrite-prompt: the rewriter's system prompt version (REWRITE_PROMPTS; rewrite_prompt_file)."""
    parser.add_argument("--rewrite-prompt", choices=REWRITE_PROMPTS, default=default,
                        help=f"rewriter prompt: v1 = rewrite-v1.md, v2 = rewrite-v2.md (default {default})")


def rewrite_prompt_file(version: str) -> str:
    """The prompt file under kosis_rag/rewrite/prompts of a --rewrite-prompt version: v1 -> PROMPT_FILE, v2 ->
    PROMPT_FILE_V2. KeyError for another version."""
    from kosis_rag.rewrite.constants import PROMPT_FILE, PROMPT_FILE_V2

    return {"v1": PROMPT_FILE, "v2": PROMPT_FILE_V2}[version]


def row_preset(row: dict) -> str:
    """The preset a rewrites.jsonl row was made with; rows written before --preset existed say none: "staged"."""
    return row.get("preset") or DEFAULT_PRESET


def apply_search_settings(args: Any) -> None:
    """Load .env (unless --no-env-file) and put the search arguments into os.environ over it and the shell: evals.
    run_eval.apply_settings, so the settings resolve as in an L1 run. Call before stage1_resources / reading a config."""
    from evals.run_eval import apply_settings

    apply_settings(args)


def stage1_resources(preset: str = DEFAULT_PRESET) -> Any:
    """The search resources of an L1 preset with stage 2 off (PRESETS; default "staged" = graph:staged-gpt with stage 2
    off: condition filter on, stage 1 from RERANK_CKPT, no LLM), built the way the L1 runner builds them
    (evals.runners.retrieval._resources over evals.config.search_config). Call apply_search_settings first.

    Side effects: reads tables.jsonl, connects to Chroma, loads the embedder and the cross-encoder. SystemExit for a
    preset not in PRESETS, and without a stage 1 checkpoint (the trigger score needs stage 1).
    """
    from evals.runners.retrieval import _resources

    if preset not in PRESETS:
        raise SystemExit(f"unknown preset {preset!r}; known: {', '.join(PRESETS)}")
    resources = _resources(preset)
    if resources.stage1_score is None:
        raise SystemExit("stage 1 is off: give --rerank-ckpt (or RERANK_CKPT); the rewrite trigger reads its score")
    return resources


def stage2_resources(preset: str) -> Any:
    """The search resources of STAGE2_PRESETS[preset] (e.g. staged-condyears -> staged-gpt-condyears), built exactly
    as the L1 runner builds graph:<that preset> (evals.runners.retrieval._resources: the shell's STAGE2_MODEL /
    STAGE2_BASE_URL / STAGE2_API_KEY through evals.config.stage2_llm). Call apply_search_settings first.

    Out: SearchResources with stage 1 and the stage 2 selector (rerank.hcx.HcxSelector for "gpt").
    Side effects: as stage1_resources. SystemExit for an unknown preset, without stage 1, or without a stage 2 key
        (kosis_rag.llm.config.require_key, the check the rerank2 node makes on entry).
    """
    from kosis_rag.llm.config import LLMKeyMissing, require_key

    from evals.runners.retrieval import _resources

    if preset not in STAGE2_PRESETS:
        raise SystemExit(f"unknown preset {preset!r}; known: {', '.join(STAGE2_PRESETS)}")
    resources = _resources(STAGE2_PRESETS[preset])
    if resources.stage1_score is None:
        raise SystemExit("stage 1 is off: give --rerank-ckpt (or RERANK_CKPT)")
    try:
        require_key(resources.stage2_llm)
    except LLMKeyMissing as exc:
        raise SystemExit(f"stage 2: {exc} (set STAGE2_API_KEY for the stage 2 server)") from exc
    return resources


def without_stage2(resources: Any) -> Any:
    """The same resources with stage 2 off (select None): build_graph then ends at rerank1 -> gate, the stage 1 pass.
    Every other part (records, lists, stage 1 scorer, options) is the same object. Pure (a shallow copy)."""
    import dataclasses

    return dataclasses.replace(resources, select=None, stage2_llm=None)


def stage1_pass(graph: Any, question: str) -> Any:
    """The search up to stage 1 for one question: the compiled search graph over stage1_resources (parse ->
    condition -> lists -> fuse -> rerank1 -> gate; no stage 2), i.e. the graph's own step functions.

    Out: the final kosis_rag SearchState: query (parsed), table_filter, runs / run_removed (each list), docs (the whole
    pool in stage 1 order, stage1_score on the re-scored head), trace. Side effects: Chroma query, embedder and
    cross-encoder inference.
    """
    from kosis_rag.graph.search import run_search

    return run_search(graph, question)


def stage1_signals(state: Any) -> tuple[float | None, float | None]:
    """(stage1_top_score, stage1_margin) of a stage1_pass state: the highest stage1_score over the whole pool and its
    gap to the second, exactly as the L1 runner records them (evals.runners.retrieval.stage1_signals)."""
    from evals.runners.retrieval import stage1_signals as signals

    return signals(state.docs)


def search_rewrite(rewrite: str, table_filter: Any, resources: Any) -> list[list[Any]]:
    """The rewrite searched in the REWRITE_LISTS (dense, BM25) under the ORIGINAL question's table filter (not parsed
    again from the rewrite, plan §4.1), each cut to options.pool (retrieve.node.retrieve_list).

    Out: one ranked Document list per REWRITE_LISTS name, in that order. Side effects: Chroma query (and the query
    embedder), BM25 scoring.
    """
    from kosis_rag.retrieve import node as retrieve_step
    from kosis_rag.retrieve.schemas import ListInput

    return [retrieve_step.retrieve_list(name, ListInput(question=rewrite, table_filter=table_filter), resources).docs
            for name in REWRITE_LISTS]


def fuse_rewrite(runs: dict[str, list], again: list[list[Any]], weight: float, resources: Any) -> list[Any]:
    """Weighted RRF of pass 1's lists (their own weights, fuse order) and the rewrite lists (weight `weight` each,
    named <list>_rw): retrieve.fuse.rrf_fuse with pool = options.pool and the dense list's score kept (score_from 0),
    as the graph's fuse node does with pass 1's lists alone.

    In: runs = the stage 1 pass's state.runs (list name -> docs); again = search_rewrite(...). Out: the fused pool.
    Side effects: none.
    """
    from kosis_rag.retrieve import node as retrieve_step
    from kosis_rag.retrieve.fuse import rrf_fuse

    lists = retrieve_step.search_lists(resources)
    names = [name for name, _, _ in lists] + [f"{name}_rw" for name in REWRITE_LISTS][: len(again)]
    return rrf_fuse([runs.get(name, []) for name, _, _ in lists] + list(again), names, k=resources.options.pool,
                    score_from=0, weights=[w for _, _, w in lists] + [weight] * len(again))


def ckpt_name(path: Any) -> str | None:
    """A checkpoint path as run identities compare it: its file name, lower-cased, so a relative or absolute
    spelling or another separator of the same file compares equal. None or "" gives None. Pure."""
    if path is None or str(path) == "":
        return None
    return str(path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()


def rewrite_search(resources: Any) -> dict:
    """The search an R2 rewrite depended on (its table names come from pass 1's top 10): collection and the
    embedder / re-ranker checkpoints (ckpt_name). Written as "search" in rewrite rows; rw_eval's cache matches on it
    (same_search). Pure (reads resources.spec)."""
    spec = resources.spec
    return {"collection": spec.chroma.collection, "embed_ckpt": ckpt_name(spec.embed_ckpt),
            "rerank_ckpt": ckpt_name(spec.rerank_ckpt)}


def same_search(row_search: dict | None, search: dict | None) -> bool:
    """A cached rewrite row's "search" matches the current one: every field equal, and a field the row does not
    carry (rows made before "search" was written: none) matches anything. Pure."""
    if not search or not row_search:
        return True
    return all(row_search.get(k) in (None, v) for k, v in search.items())


def table_names(resources: Any, keys: Iterable[str]) -> list[str]:
    """Registry table names (TableInfo.table_name) of the keys, in order; keys not in the registry are skipped."""
    return [resources.records[k].info.table_name for k in keys if k in resources.records]
