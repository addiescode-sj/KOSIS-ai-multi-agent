"""KOS-192 P2: rewrite every dev question twice (R1: the question; R2: and the top 10 stage 1 table names) with the
stage 2 LLM server, check each rewrite by code, and summarize. Dev split by default; plan 07 Q5 also rewrites the test
split (--split test | all with --allow-test), whose content is never printed.

    set PYTHONPATH=.;src;vendor\\phase2\\src;vendor\\phase1;vendor\\phase1\\src
    python scripts\\kos192\\gen_rewrites.py --collection <collection> --embed-ckpt <embedder .pt> --rerank-ckpt <re-ranker .pt> [--build v1] [--limit 5] [--allow-remote]
    python scripts\\kos192\\gen_rewrites.py ... --preset staged-condyears --variants R2 --build v2-condyears
    python scripts\\kos192\\gen_rewrites.py ... --preset staged-condyears --variants R2 --build v2-condyears --split test --allow-test
    python scripts\\kos192\\gen_rewrites.py ... --preset staged-condyears --variants R2 --rewrite-prompt v2 --build v3-condyears-p2

Search: the L1 preset --preset (default "staged" = graph:staged-gpt with stage 2 off: condition filter on, stage 1
from --rerank-ckpt / RERANK_CKPT; "staged-condyears" = the B1 baseline's search, only years / period filter), built
as the L1 runner builds it; each question runs the search graph up to stage 1 (parse ->
condition -> lists -> fuse -> rerank1). R2 shows the rewriter the registry names of the first 10 tables in stage 1
order (MAX_TABLE_NAMES). Rewriter: kosis_rag.rewrite (prompt --rewrite-prompt: v1 = rewrite-v1.md, the default, so
the v1 / v2-condyears rows still resume; v2 = rewrite-v2.md, whose --build defaults to v3-condyears-p2;
temperature 0, MAX_TOKENS, 30 s timeout unless set) on the stage 2 "gpt" config of the service (STAGE2_MODEL /
STAGE2_BASE_URL / STAGE2_API_KEY, evals.config.stage2_llm), with --rewrite-model / --rewrite-base-url over it. The plan fixes the local Gemma server, so
a config without a base URL (OpenAI itself) is refused unless --allow-remote. A rewrite the model gives is parsed
(parse.node.parse_question) and checked (rewrite.node.check_rewrite); a failed check is status "rejected" with its
reason.

Writes data/lab/rewrite/<build>/rewrites.jsonl (one line per qid x variant: qid, variant, rewrite, status, reason,
seconds, input_tokens, output_tokens, stage1_top_score, stage1_margin, prompt_sha (sha256 of the system prompt),
model, preset, split, prompt (the prompt file); dev and, with --allow-test, test content in one file) and
summary.json (aggregates, with the preset, prompt and variants_made). --variants limits the variants made (default
both; R1 does not depend on the preset, so plan 07 Q1 makes only R2 and reuses v1's R1).
A rerun skips a qid x variant only when its last row of the current prompt, model and preset is not an error
(errors, and rows of another prompt, model or preset, are asked again; a row without a preset is "staged"); the
summary reads that last row per qid x variant. Prints aggregates only, per split (dev, test): per variant the share used /
none / rejected (by reason) / error for answerable and no-answer items, rewrites identical to the question, latency
p50 / p95, and C5: the mean bigram overlap of the question with its gold table name before and after the rewrite.
The console names a dev qid with its statuses; a test item shows a counter only (no qid, status, question or rewrite).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "vendor" / "phase1" / "src", ROOT / "vendor" / "phase1", ROOT / "vendor" / "phase2" / "src",
           ROOT / "src", ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
os.environ.setdefault("KOSIS_WORKSPACE", str(ROOT))

from kos192_lib import (DEFAULT_PRESET, GOLDEN, LAB, REWRITE_BUILDS, VARIANTS, add_preset_arg,  # noqa: E402
                        add_search_args, add_rewrite_prompt_arg, apply_search_settings, best_overlap, fmt, load_golden,
                        mean, quantile, read_jsonl, rewrite_prompt_file, rewrite_search, row_preset, share,
                        stage1_pass, stage1_resources, stage1_signals, table_names, utf8_stdout, write_json)

STATUSES = ("used", "none", "rejected", "error")


SPLITS = ("dev", "test", "all")


def check_split(split: str, allow_test: bool = False) -> None:
    """dev, or test / all with allow_test (plan 07 Q5: the test split is rewritten for its one final run). SystemExit
    otherwise, before anything is loaded."""
    if split == "dev" or (split in ("test", "all") and allow_test):
        return
    if split in ("test", "all"):
        raise SystemExit(f"--split {split} sends test questions to the rewriter: pass --allow-test for the one final "
                         "test run (plan 07 Q5)")
    raise SystemExit(f"--split must be one of {', '.join(SPLITS)}, not {split!r}")


def splits_of(split: str) -> tuple[str, ...]:
    """The splits a --split value covers, in report order. Pure."""
    return ("dev", "test") if split == "all" else (split,)


def prompt_sha(text: str) -> str:
    """sha256 of the system prompt text (UTF-8): which prompt a row was made with."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def current_rows(rows: list[dict], sha: str, model: str, preset: str = DEFAULT_PRESET) -> list[dict]:
    """The last row per (qid, variant) among the rows made with this prompt sha, model and preset (a row without a
    preset counts as "staged", kos192_lib.row_preset), in first-seen order. Pure."""
    last: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r.get("prompt_sha") == sha and r.get("model") == model and row_preset(r) == preset:
            last[(r["qid"], r["variant"])] = r
    return list(last.values())


def done_pairs(rows: list[dict], sha: str, model: str, preset: str = DEFAULT_PRESET) -> set[tuple[str, str]]:
    """(qid, variant) pairs a resumed run skips: their last row of this prompt, model and preset is not an error.
    Pure."""
    return {(r["qid"], r["variant"]) for r in current_rows(rows, sha, model, preset) if r["status"] != "error"}


def parse_variants(text: str) -> tuple[str, ...]:
    """--variants "R1,R2" -> the variants in VARIANTS order; SystemExit for an unknown or empty list. Pure."""
    asked = {v.strip() for v in text.split(",") if v.strip()}
    unknown = asked - set(VARIANTS)
    if unknown or not asked:
        raise SystemExit(f"--variants: give one or more of {', '.join(VARIANTS)} (got {text!r})")
    return tuple(v for v in VARIANTS if v in asked)


def rewrite_checked(question: str, names: list[str], client, original_parsed, resources, *, prompt: str | None = None):
    """One rewrite of the question (R1: names = [], R2: the stage 1 names), checked by code; prompt = the system
    prompt file passed to rewrite_question (None: its default, PROMPT_FILE = rewrite-v1.md).

    Out: kosis_rag.rewrite.schemas.RewriteOutput; a "used" rewrite that fails check_rewrite comes back as "rejected"
        with check_rewrite's reason (the rewrite text kept). Side effects: one LLM call; parse rules only otherwise.
    """
    from kosis_rag.parse import node as parse_step
    from kosis_rag.parse.schemas import ParseInput
    from kosis_rag.rewrite.node import check_rewrite, rewrite_question
    from kosis_rag.rewrite.schemas import RewriteInput

    from kosis_rag.rewrite.constants import PROMPT_FILE

    out = rewrite_question(RewriteInput(question=question, table_names=names), client, prompt=prompt or PROMPT_FILE)
    if out.status == "used":
        parsed = parse_step.parse_question(ParseInput(question=out.rewrite), resources)
        reason = check_rewrite(question, out.rewrite, original_parsed, parsed)
        if reason is not None:
            out = out.model_copy(update={"status": "rejected", "reason": reason})
    return out


def rewrite_row(item, variant: str, out, *, top: float | None, margin: float | None, sha: str, model: str,
                preset: str, wall_seconds: float | None = None, prompt: str | None = None,
                search: dict | None = None) -> dict:
    """One rewrites.jsonl row (module docstring) for a RewriteOutput; rw_eval writes its inline rewrites with it too,
    adding wall_seconds (the rewrite step's measured time: call, parse and check). prompt = the prompt file (None:
    not recorded, as before --rewrite-prompt; prompt_sha tells it either way); search = kos192_lib.rewrite_search
    (written as "search" when given). Pure."""
    row = {"qid": item.qid, "variant": variant, "rewrite": out.rewrite, "status": out.status, "reason": out.reason,
           "seconds": out.seconds, "input_tokens": out.input_tokens, "output_tokens": out.output_tokens,
           "stage1_top_score": top, "stage1_margin": margin, "prompt_sha": sha, "model": model, "preset": preset,
           "split": item.split, "prompt": prompt}
    if wall_seconds is not None:
        row["wall_seconds"] = wall_seconds
    if search is not None:
        row["search"] = search
    return row


def summarize(lines: list[dict], items: dict, names_of: dict[str, str],
              variants: tuple[str, ...] = VARIANTS) -> dict:
    """Aggregates of the rewrites file per variant (`variants`, default both). Pure.

    In: lines = rewrites.jsonl rows, one per qid x variant (main passes current_rows: the last row of the current
        prompt and model); items = qid -> RetrievalItem (dev; lines of other qids are left out); names_of =
        table key -> registry name.
    Out: variant -> {"n", "status": {"all" | "answerable" | "no_answer": {status: count}}, "rejected_by_reason",
        "given" (the model gave a rewrite: used + rejected), "identical" (of those, equal to the question after
        rewrite.node.normalize), "latency_p50_s", "latency_p95_s", "c5": {"n" (answerable, used), "before_mean",
        "after_mean", "went_down"}}. C5 overlap = kos192_lib.best_overlap of the question / rewrite with the names of
        the gold keys (any slot, any alternative).
    """
    from kosis_rag.rewrite.node import normalize

    out: dict[str, dict] = {}
    for variant in variants:
        rows = [r for r in lines if r["variant"] == variant and r["qid"] in items]
        status = {g: {s: 0 for s in STATUSES} for g in ("all", "answerable", "no_answer")}
        rejected: dict[str, int] = {}
        given = identical = 0
        before, after, down = [], [], 0
        for r in rows:
            item = items[r["qid"]]
            for g in ("all", "answerable" if item.answerable else "no_answer"):
                status[g][r["status"]] = status[g].get(r["status"], 0) + 1
            if r["status"] == "rejected":
                rejected[r["reason"]] = rejected.get(r["reason"], 0) + 1
            if r["status"] in ("used", "rejected") and r.get("rewrite") is not None:
                given += 1
                identical += int(normalize(r["rewrite"]) == normalize(item.question))
            if r["status"] == "used" and item.answerable:
                names = [names_of[k] for k in sorted(item.gold_keys) if k in names_of]
                b, a = best_overlap(item.question, names), best_overlap(r["rewrite"], names)
                if b is not None and a is not None:
                    before.append(b)
                    after.append(a)
                    down += int(a < b)
        seconds = [r["seconds"] for r in rows if r.get("seconds") is not None]
        out[variant] = {"n": len(rows), "status": status, "rejected_by_reason": dict(sorted(rejected.items())),
                        "given": given, "identical": identical,
                        "latency_p50_s": quantile(seconds, 0.5), "latency_p95_s": quantile(seconds, 0.95),
                        "c5": {"n": len(before), "before_mean": mean(before), "after_mean": mean(after),
                               "went_down": down}}
    return out


def render(summary: dict, split: str = "dev") -> str:
    """The markdown report of summarize() for one split (aggregates only)."""
    lines = [f"# KOS-192 P2 재작성 생성 ({split})", "",
             "- used = 검사 통과, none = 모델이 '바꿀 것 없음', rejected = 코드 검사 탈락, error = 호출·응답 오류.",
             "- 원문과 같음 = 모델이 낸 재작성(used+rejected) 중 공백·문장부호를 빼면 질문과 같은 것.",
             "- C5 = 정답 표 이름(OR 그룹 중 최고)의 글자 bigram 중 질문에 있는 비율, 재작성 전 → 후 (답 있음, used)."]
    for variant, s in summary.items():
        st = s["status"]
        lines += ["", f"## {variant} (n {s['n']})", "", "| 문항 | n | used | none | rejected | error |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for g in ("all", "answerable", "no_answer"):
            n = sum(st[g].values())
            lines.append(f"| {g} | {n} | " + " | ".join(share(st[g][x], n) for x in STATUSES) + " |")
        c5 = s["c5"]
        lines += ["",
                  "- rejected 사유: " + (", ".join(f"{k} {v}" for k, v in s["rejected_by_reason"].items()) or "없음"),
                  f"- 원문과 같음: {share(s['identical'], s['given'])}",
                  f"- 지연(재작성 호출): p50 {fmt(s['latency_p50_s'], 2)} s · p95 {fmt(s['latency_p95_s'], 2)} s",
                  f"- C5 bigram 겹침 (n {c5['n']}): {fmt(c5['before_mean'])} → {fmt(c5['after_mean'])}, "
                  f"줄어든 재작성 {share(c5['went_down'], c5['n'])}"]
    return "\n".join(lines) + "\n"


def default_build(rewrite_prompt: str) -> str:
    """--build when not given: "v1" for prompt v1 (the existing rows resume there), else rw_eval's build for the
    prompt (REWRITE_BUILDS: v2 -> v3-condyears-p2), so the two prompts' rows never share a file by default. Pure."""
    return "v1" if rewrite_prompt == "v1" else REWRITE_BUILDS[rewrite_prompt]


def rewriter_config(args):
    """The rewriter's LLMConfig: the stage 2 "gpt" config of the service (evals.config.stage2_llm: STAGE2_MODEL,
    STAGE2_BASE_URL, STAGE2_API_KEY or OPENAI_API_KEY), with --rewrite-model / --rewrite-base-url over it.
    Call after apply_search_settings (it reads os.environ). SystemExit when no key is set, or when there is no base URL
    (the OpenAI API itself) and --allow-remote is not given."""
    from kosis_rag.llm.config import LLMKeyMissing, require_key

    from evals.config import stage2_llm

    config = stage2_llm("gpt")
    update = {k: v for k, v in (("model", args.rewrite_model), ("base_url", args.rewrite_base_url)) if v}
    config = config.model_copy(update=update) if update else config
    if not config.base_url and not args.allow_remote:
        raise SystemExit("rewriter: no base URL (STAGE2_BASE_URL or --rewrite-base-url): the plan uses the local Gemma "
                         "server; pass --allow-remote to call the OpenAI API instead")
    try:
        require_key(config)
    except LLMKeyMissing as exc:
        raise SystemExit(f"rewriter: {exc} (set STAGE2_API_KEY for the stage 2 server)") from exc
    return config


def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    pre = argparse.ArgumentParser(add_help=False)  # the split is refused before anything is loaded
    pre.add_argument("--split", default="dev")
    pre.add_argument("--allow-test", action="store_true")
    known = pre.parse_known_args(argv)[0]
    check_split(known.split, known.allow_test)
    parser = argparse.ArgumentParser(description="KOS-192 P2: R1 / R2 rewrites of a split, checked by code.")
    parser.add_argument("--golden", type=Path, default=GOLDEN, help="retrieval golden file (default: the KOS-192 set)")
    parser.add_argument("--split", default="dev", help="dev (default), or test / all with --allow-test")
    parser.add_argument("--allow-test", action="store_true", help="allow --split test | all (plan 07 Q5)")
    parser.add_argument("--build", help="output folder name under data/lab/rewrite (default: v1 for --rewrite-prompt "
                        f"v1, {REWRITE_BUILDS['v2']} for v2)")
    parser.add_argument("--limit", type=int, help="only the first N questions of the split (smoke run)")
    parser.add_argument("--rewrite-model", help="rewriter model (default: STAGE2_MODEL, the stage 2 gpt model)")
    parser.add_argument("--rewrite-base-url", help="rewriter server (default: STAGE2_BASE_URL)")
    parser.add_argument("--allow-remote", action="store_true", help="allow a rewriter config without a base URL (OpenAI)")
    parser.add_argument("--variants", default=",".join(VARIANTS), help="variants to make, comma separated (default R1,R2)")
    add_preset_arg(parser)
    add_rewrite_prompt_arg(parser, default="v1")  # v1: the existing v1 / v2-condyears rows resume
    add_search_args(parser)
    args = parser.parse_args(argv)
    check_split(args.split, args.allow_test)
    args.build = args.build or default_build(args.rewrite_prompt)
    variants = parse_variants(args.variants)
    splits = splits_of(args.split)

    from kosis_rag.graph.search import build_graph
    from kosis_rag.rewrite.constants import MAX_TABLE_NAMES
    from kosis_rag.rewrite.node import rewrite_client, system_prompt

    apply_search_settings(args)
    config = rewriter_config(args)
    prompt = rewrite_prompt_file(args.rewrite_prompt)
    sha = prompt_sha(system_prompt(prompt))
    print(f"rewriter: model {config.model} · base URL {config.base_url or '(none: OpenAI API)'} · "
          f"prompt {prompt} sha256 {sha[:12]} · preset {args.preset} · variants {','.join(variants)}")
    items = {q: i for q, i in load_golden(args.golden).items() if i.split in splits}
    todo_items = list(items.values())[: args.limit] if args.limit else list(items.values())
    resources = stage1_resources(args.preset)
    graph, client = build_graph(resources), rewrite_client(config)
    search = rewrite_search(resources)
    out_dir = LAB / args.build
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "rewrites.jsonl"
    done = done_pairs(read_jsonl(path), sha, config.model, args.preset)
    failed = 0
    with path.open("a", encoding="utf-8") as f:
        for n, item in enumerate(todo_items, 1):
            todo = [v for v in variants if (item.qid, v) not in done]
            if not todo:
                continue
            started = time.perf_counter()
            dev = item.split == "dev"  # a test item is shown as a counter only
            try:
                state = stage1_pass(graph, item.question)
            except Exception as exc:  # retried on the next run (nothing written for it)
                failed += 1
                detail = f"{item.qid} search failed: {type(exc).__name__}: {exc}" if dev else "search failed"
                print(f"[{n}/{len(todo_items)}] {detail}", file=sys.stderr)
                continue
            top, margin = stage1_signals(state)
            names = table_names(resources, [d.metadata["key"] for d in state.docs[:MAX_TABLE_NAMES]])
            statuses = []
            for variant in todo:
                out = rewrite_checked(item.question, names if variant == "R2" else [], client, state.query, resources,
                                      prompt=prompt)
                f.write(json.dumps(rewrite_row(item, variant, out, top=top, margin=margin, sha=sha, model=config.model,
                                               preset=args.preset, prompt=prompt, search=search),
                                   ensure_ascii=False) + "\n")
                f.flush()
                statuses.append(f"{variant} {out.status}")
            if dev:
                print(f"[{n}/{len(todo_items)}] {item.qid} {' · '.join(statuses)} ({time.perf_counter() - started:.1f}s)")
            else:
                print(f"[{n}/{len(todo_items)}]")

    names_of = {k: r.info.table_name for k, r in resources.records.items()}
    # the summary covers every split of the golden file the build has rows for (a test run keeps the dev aggregates)
    current = current_rows(read_jsonl(path), sha, config.model, args.preset)
    golden_all = load_golden(args.golden)
    have = {golden_all[r["qid"]].split for r in current if r["qid"] in golden_all}
    by_split = {s: summarize(current, {q: i for q, i in golden_all.items() if i.split == s}, names_of, variants)
                for s in ("dev", "test") if s in have or s in splits}
    write_json(out_dir / "summary.json", {
        "build": args.build, "preset": args.preset, "variants_made": list(variants), "split_run": args.split,
        "prompt": prompt, "prompt_sha": sha, "model": config.model,
        "base_url": config.base_url, "golden": str(args.golden), "search_failed": failed,
        "variants": by_split.get("dev", {}), "by_split": by_split})
    for split, summary in by_split.items():
        print(render(summary, split), end="")
    print(f"-> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
