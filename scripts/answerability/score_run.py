"""Answerability classifier (KOS-193 draft): score evals L1 runs' shown tables with a trained checkpoint and report
the refusal numbers with and without the classifier. No search and no stage-2 call: each run's results.jsonl is reused.

Per question (results.jsonl row joined with the golden files by qid):
    picks     the tables stage 2 picked (the first stage2_picked_n of `keys`); a stage-2 error -> the first --top keys
    top       the first --top keys (stage-1 order after stage 2), the classifier alone
    s_picks   max classifier logit over picks; s_top: max over top (one scoring pass, card text: cards.card on
              data/tables.jsonl)
Refusal (positive class = not answerable), 04-dataset-plan.md §6.4 / §9 D2:
    gemma         the run's own no_answer_pred
    combined      gemma OR every picked table < theta (s_picks < theta)              <- the planned service rule
    combined_top  gemma OR (every picked table < theta AND every table of the first --top keys < theta), i.e.
                  max(s_picks, s_top) < theta; a row with no picks is refused by gemma only (as combined), so
                  combined_top never refuses a row combined keeps
    alone         s_top < theta
    A row with no keys at all (no score) is refused by gemma only under every rule; such rows that gemma keeps are
    counted (no_keys_not_refused), the semantics are not changed.
Sets (from the golden notes): a JSON-object notes (build_dev_na.py v2) gives set / sub / relative -> groups <set>,
<set>:<sub> and <set>:relative; v1 dev-NA+ notes ("src=... sub=...") -> set Dv1; other plain notes -> set A when
answerable (KOS-192 dev) and B when not (old golden no-answer rows: add eval/golden/retrieval_golden.jsonl to --golden
and to the run, else B is empty).
Fitting (dev only; plan §6.1: theta is set on A + C, D is reference only): rows of --fit-exclude-sets (default D Dv1)
and rows with a relative time (unless --fit-include-relative) are left out of every fitted theta and of the top-level
numbers; they still appear in by_group. The summary gives n_fit_answerable / n_fit_unanswerable.
    budget        per rule, the highest theta with false refusals on fitted answerable rows <= --max-false-refusals
                  (default 3), and the numbers at it (recall overall / per no_answer_reason / per group, false
                  refusals, Hit@1 kept, answerable-vs-unanswerable AUC of the rule's max score)
    best F1       the best-F1 theta of `combined` (candidates include min score = no classifier refusal)
theta (--theta, else the checkpoint .json threshold_best_f1) is also reported. --split test needs --allow-test and uses
the given / checkpoint theta only, never one fitted on test.
AUC: per rule over the rows that rule can score (n_pos / n_neg given) and over the common subset (rows with both
s_picks and s_top), so the rules can be compared on the same rows.
With several --run folders each run is reported alone and in a pooled block (rows of all runs, a qid seen in an
earlier run is dropped; groups run:<full run path>), e.g. "answerable 285 = A 85 (KOS-192 run) + C4 (dev_na_v2 run)".
Output: <run>/answerability/<ckpt>-<split>.json (aggregates); for dev also <ckpt>-dev.scores.jsonl (qid, scores);
with several runs the pooled block goes to --out (default <first run>/answerability/<ckpt>-<split>-pooled.json).

    python scripts\\answerability\\score_run.py --selftest
    python scripts\\answerability\\score_run.py --run evals\\reports\\<run> --ckpt data\\models\\answerability\\ANS-rr-e2.pt
    python scripts\\answerability\\score_run.py --run evals\\reports\\<kos192 run> evals\\reports\\<dev_na_v2 run> --ckpt data\\models\\answerability\\ANS-rr-e2.pt
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_data import read_jsonl  # noqa: E402
from cards import CARD_FORMAT, card, load_records  # noqa: E402

RULES = ("gemma", "combined", "combined_top", "alone")
GEMMA_RULES = ("gemma", "combined", "combined_top")
FIT_EXCLUDE = ("D", "Dv1")


def f1_block(pred: list[bool], gold: list[bool]) -> dict:
    tp = sum(p and g for p, g in zip(pred, gold))
    fp = sum(p and not g for p, g in zip(pred, gold))
    fn = sum(g and not p for p, g in zip(pred, gold))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {"f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0, "precision": round(prec, 4),
            "recall": round(rec, 4), "refused_right": tp, "false_refusals": fp, "missed": fn}


def rule_score(r: dict, rule: str) -> float | None:
    """The score the rule compares with theta (None: the classifier cannot refuse this row)."""
    if rule == "combined":
        return r["s_picks"]
    if rule == "combined_top":
        return None if r["s_picks"] is None or r["s_top"] is None else max(r["s_picks"], r["s_top"])
    if rule == "alone":
        return r["s_top"]
    return None


def refuses(r: dict, rule: str, theta: float) -> bool:
    if rule in GEMMA_RULES and r["gemma"]:
        return True
    s = rule_score(r, rule)
    return s is not None and s < theta


def groups_of(r: dict) -> list[str]:
    return r.get("groups") or ["other"]


def report(rows: list[dict], theta: float, group_rows: list[dict] | None = None) -> dict:
    """rows: dicts with answerable, reason, hit1, gemma (bool), s_picks, s_top (float | None), groups (optional).
    Top-level numbers over rows; by_group over group_rows (default rows; e.g. fitted rows + reference sets)."""
    gold = [not r["answerable"] for r in rows]
    out = {"theta": theta, "n": len(rows), "not_answerable": sum(gold)}
    n_ans = sum(r["answerable"] for r in rows)
    for name in RULES:
        pred = [refuses(r, name, theta) for r in rows]
        b = f1_block(pred, gold)
        by, rr = Counter(), Counter()
        for r, p in zip(rows, pred):
            if not r["answerable"]:
                by[f"{r['reason']}:{'refused' if p else 'missed'}"] += 1
                rr[r["reason"]] += 1
        grp: dict[str, Counter] = {}
        for r in (rows if group_rows is None else group_rows):
            p = refuses(r, name, theta)
            for g in groups_of(r):
                c = grp.setdefault(g, Counter())
                c["n"] += 1
                if r["answerable"]:
                    c["answerable"] += 1
                    c["false_refusals"] += p
                else:
                    c["unanswerable"] += 1
                    c["refused_right"] += p
        b["by_reason"] = dict(sorted(by.items()))
        b["recall_by_reason"] = {k: round(by[f"{k}:refused"] / n, 4) for k, n in sorted(rr.items())}
        b["by_group"] = {g: {**c, "recall": round(c["refused_right"] / c["unanswerable"], 4) if c["unanswerable"]
                             else None} for g, c in sorted(grp.items())}
        b["hit1_answerable"] = round(sum(r["hit1"] and not p for r, p in zip(rows, pred) if r["answerable"])
                                     / max(1, n_ans), 4)
        out[name] = b
    return out


def best_theta(rows: list[dict], rule: str = "combined") -> float | None:
    """Best-F1 theta of a rule; the first candidate, min score, means "the classifier refuses nothing"."""
    sc = sorted({s for s in (rule_score(r, rule) for r in rows) if s is not None})
    if not sc:
        return None
    best, t_best = -1.0, None
    for t in [sc[0]] + [s + 1e-6 for s in sc]:
        f = report(rows, t)[rule]["f1"]
        if f > best:
            best, t_best = f, t
    return t_best


def budget_theta(rows: list[dict], rule: str, max_fr: int) -> dict:
    """Highest theta whose false refusals (answerable rows refused) are <= max_fr. Refusal is strict (score < theta),
    so with the answerable scores the rule can refuse sorted e[0] <= e[1] <= ..., and b = max_fr minus gemma's own
    false refusals, theta = e[b] refuses at most e[0..b-1]. Fewer than b+1 such scores: any theta fits (unbounded,
    reported as just above the highest score of all rows). Gemma alone over the budget: theta None."""
    ans = [r for r in rows if r["answerable"]]
    g0 = sum(r["gemma"] for r in ans) if rule in GEMMA_RULES else 0
    if rule == "gemma":
        return {"theta": None, "gemma_false_refusals": g0, "unbounded": False}
    b = max_fr - g0
    if b < 0:
        return {"theta": None, "gemma_false_refusals": g0, "unbounded": False, "why": "gemma alone exceeds the budget"}
    es = sorted(s for r in ans if not (rule in GEMMA_RULES and r["gemma"])
                for s in [rule_score(r, rule)] if s is not None)
    if len(es) > b:
        return {"theta": es[b], "gemma_false_refusals": g0, "unbounded": False}
    every = [s for s in (rule_score(r, rule) for r in rows) if s is not None]
    return {"theta": (max(every) + 1e-6) if every else None, "gemma_false_refusals": g0, "unbounded": True}


def auc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 4)


def rule_auc(rows: list[dict], rule: str) -> dict:
    """Answerable (higher) vs unanswerable max-score AUC of the score the rule compares, rows it can score only."""
    sc = [(r["answerable"], rule_score(r, rule)) for r in rows]
    pos = [s for a, s in sc if a and s is not None]
    neg = [s for a, s in sc if not a and s is not None]
    return {"auc": auc(pos, neg), "n_pos": len(pos), "n_neg": len(neg)}


def auc_block(rows: list[dict]) -> dict:
    common = [r for r in rows if r["s_picks"] is not None and r["s_top"] is not None]
    return {"all_scored": {k: rule_auc(rows, k) for k in RULES[1:]},
            "common_subset": {k: rule_auc(common, k) for k in RULES[1:]}}


def budget_report(rows: list[dict], max_fr: int, group_rows: list[dict] | None = None) -> dict:
    """Per rule: the budget theta fitted on rows and that rule's numbers at it (§6.4)."""
    out = {"max_false_refusals": max_fr, "n_answerable": sum(r["answerable"] for r in rows)}
    for rule in RULES:
        fit = budget_theta(rows, rule, max_fr)
        t = fit["theta"]
        if rule == "gemma":
            block = report(rows, 0.0, group_rows)["gemma"]
        else:
            block = report(rows, t, group_rows)[rule] if t is not None else None
        out[rule] = {**fit, "auc_max_score": rule_auc(rows, rule), "at_theta": block}
    return out


class Scorer:
    """The trained cross-encoder: base model (from the checkpoint .json) + the checkpoint tensors put back by name."""

    def __init__(self, ckpt: Path, batch: int = 16, max_length: int = 512):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.meta = json.loads(ckpt.with_suffix(".json").read_text(encoding="utf-8"))
        if self.meta.get("card_format") != CARD_FORMAT:
            raise ValueError(f"{ckpt.name} was trained on {self.meta.get('card_format')!r} cards, cards.py is {CARD_FORMAT!r}")
        self.torch = torch
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.tok = AutoTokenizer.from_pretrained(self.meta["base"])
        model = AutoModelForSequenceClassification.from_pretrained(self.meta["base"], num_labels=1).float()
        res = model.load_state_dict(torch.load(ckpt, map_location="cpu"), strict=False)
        if res.unexpected_keys:
            raise ValueError(f"{ckpt.name} does not fit {self.meta['base']}: {res.unexpected_keys[:3]}")
        self.model = model.to(self.device).eval()
        self.batch = batch
        self.max_length = int(self.meta.get("max_length") or max_length)  # the trainer's length when recorded

    def __call__(self, pairs: list[tuple[str, str]]) -> list[float]:
        out = []
        with self.torch.no_grad():
            for b in range(0, len(pairs), self.batch):
                ch = pairs[b:b + self.batch]
                enc = self.tok([p[0] for p in ch], [p[1] for p in ch], truncation="longest_first",
                               max_length=self.max_length, padding=True, return_tensors="pt").to(self.device)
                with self.torch.autocast(device_type=self.device.type, dtype=self.torch.float16,
                                         enabled=self.device.type == "cuda"):
                    out += self.model(**enc).logits.view(-1).float().cpu().tolist()
        return out


def note_meta(notes, answerable: bool) -> dict:
    """{"set", "sub", "relative"} of a golden row from its notes (see the module text)."""
    if isinstance(notes, str) and notes.lstrip().startswith("{"):
        try:
            d = json.loads(notes)
        except json.JSONDecodeError:
            d = None
        if isinstance(d, dict) and d.get("set"):
            return {"set": str(d["set"]), "sub": str(d.get("sub") or d.get("kind") or "-"),
                    "relative": bool(d.get("relative"))}
    if isinstance(notes, str) and notes.startswith("src="):
        return {"set": "Dv1", "sub": notes.rsplit("sub=", 1)[1].strip() if "sub=" in notes else "-", "relative": False}
    return {"set": "A" if answerable else "B", "sub": None, "relative": False}


def meta_groups(m: dict) -> list[str]:
    g = [m["set"]]
    if m["sub"]:
        g.append(f"{m['set']}:{m['sub']}")
    if m["relative"]:
        g.append(f"{m['set']}:relative")
    return g


def stage1_top(r: dict) -> list[str] | None:
    """The stage-1 re-ranker top keys as the run traced them (search_spans "rerank1" top_keys, 5 in evals runs),
    i.e. the order before stage 2 (Gemma) re-ordered `keys`. None when the run has no such span."""
    for s in r.get("search_spans") or []:
        if s.get("name") == "rerank1":
            k = (s.get("attributes") or {}).get("top_keys")
            if k:
                return list(k)
    return None


TOP_FALLBACK: Counter = Counter()


def gather(results: list[dict], golden: dict[str, dict], split: str, top: int,
           top_source: str = "keys") -> list[dict]:
    """top_source "keys": `top` = the first --top of `keys` (after stage 2); "rerank1": the stage-1 re-ranker top
    keys from the trace (classifier on stage 1 only, the no-Gemma service flow), falling back to `keys` (counted)."""
    rows = []
    for r in results:
        g = golden.get(r["qid"])
        if g is None or (split != "all" and r.get("split") != split):
            continue
        keys = r.get("keys") or []
        n = r.get("stage2_picked_n")
        picks = keys[:top] if r.get("stage2_error") or n is None else keys[:n]
        top_keys = keys[:top]
        if top_source == "rerank1":
            s1 = stage1_top(r)
            if s1 is None:
                TOP_FALLBACK["no_rerank1_span"] += 1
            else:
                top_keys = s1[:top]
                TOP_FALLBACK["rerank1"] += 1
        ans = g.get("answerable", True) is not False
        m = note_meta(g.get("notes"), ans)
        rows.append({"qid": r["qid"], "q": g["question"], "answerable": ans,
                     "reason": g.get("no_answer_reason") or "-", "hit1": bool(r.get("hit@1")),
                     "gemma": bool(r.get("no_answer_pred")), "picks": picks, "top": top_keys,
                     "set": m["set"], "relative": m["relative"], "groups": meta_groups(m)})
    return rows


def score_rows(rows: list[dict], scorer, cards_of) -> None:
    pairs, where = [], []
    for i, r in enumerate(rows):
        for k in dict.fromkeys(r["picks"] + r["top"]):
            pairs.append((r["q"], cards_of(k)))
            where.append((i, k))
    scores = scorer(pairs) if pairs else []
    per: dict[int, dict[str, float]] = {}
    for (i, k), s in zip(where, scores):
        per.setdefault(i, {})[k] = s
    for i, r in enumerate(rows):
        d = per.get(i, {})
        r["scores"] = d
        r["s_picks"] = max((d[k] for k in r["picks"]), default=None)
        r["s_top"] = max((d[k] for k in r["top"]), default=None)


def pool(blocks: list[tuple[str, list[dict]]]) -> tuple[list[dict], int]:
    """Rows of several runs in one list (group run:<label>, label = the full run path); a qid already seen in an
    earlier run is dropped."""
    seen, out, dup = set(), [], 0
    for label, rows in blocks:
        for r in rows:
            if r["qid"] in seen:
                dup += 1
                continue
            seen.add(r["qid"])
            out.append({**r, "groups": [*groups_of(r), f"run:{label}"]})
    return out, dup


def fit_rows(rows: list[dict], exclude_sets: tuple[str, ...] | list[str], include_relative: bool) -> list[dict]:
    return [r for r in rows if r.get("set") not in exclude_sets and (include_relative or not r.get("relative"))]


def summarize(rows: list[dict], theta: float, split: str, max_fr: int,
              exclude_sets: tuple[str, ...] | list[str] = FIT_EXCLUDE, include_relative: bool = False) -> dict:
    fit = fit_rows(rows, exclude_sets, include_relative)
    no_keys = Counter("answerable" if r["answerable"] else "unanswerable" for r in rows
                      if r["s_picks"] is None and r["s_top"] is None and not r["gemma"])
    out = {"n": len(rows), "n_answerable": sum(r["answerable"] for r in rows),
           "fit_exclude_sets": list(exclude_sets), "fit_include_relative": include_relative,
           "n_fit_answerable": sum(r["answerable"] for r in fit),
           "n_fit_unanswerable": sum(not r["answerable"] for r in fit),
           "n_left_out_of_fit": len(rows) - len(fit), "no_picks": sum(r["s_picks"] is None for r in rows),
           "no_keys_not_refused": dict(no_keys), "auc": auc_block(fit),
           "at_ckpt_or_given_theta": report(fit, theta, rows)}
    if split == "dev":
        t = best_theta(fit)
        out["dev_best_theta"] = t
        out["at_dev_best_theta"] = report(fit, t, rows) if t is not None else None
        out["dev_budget"] = budget_report(fit, max_fr, rows)
    return out


def selftest() -> None:
    rows = [
        {"answerable": True, "reason": "-", "hit1": True, "gemma": False, "s_picks": 3.0, "s_top": 3.0},
        {"answerable": True, "reason": "-", "hit1": True, "gemma": False, "s_picks": 0.5, "s_top": 2.0},
        {"answerable": False, "reason": "no_period", "hit1": False, "gemma": False, "s_picks": -2.0, "s_top": -1.0},
        {"answerable": False, "reason": "no_item", "hit1": False, "gemma": True, "s_picks": None, "s_top": 1.0},
    ]
    r0 = report(rows, 0.0)
    assert r0["gemma"]["f1"] == round(2 * 1 * 0.5 / 1.5, 4) and r0["combined"]["f1"] == 1.0, r0
    assert r0["combined"]["false_refusals"] == 0 and r0["combined"]["hit1_answerable"] == 1.0
    r1 = report(rows, 1.0)
    assert r1["combined"]["false_refusals"] == 1 and r1["combined"]["hit1_answerable"] == 0.5
    assert "no_period:refused" in r1["alone"]["by_reason"]
    # row 2: the picked table is wrong (0.5) but a top-5 table passes (2.0): combined refuses, combined_top keeps it
    assert r1["combined_top"]["false_refusals"] == 0 and r1["combined_top"]["refused_right"] == 2, r1["combined_top"]
    assert r1["combined_top"]["recall_by_reason"] == {"no_item": 1.0, "no_period": 1.0}
    assert r1["combined"]["by_group"]["other"]["false_refusals"] == 1
    t = best_theta(rows)
    assert report(rows, t)["combined"]["f1"] == 1.0
    # best F1 can be "no classifier refusal": gemma already refuses the only unanswerable row
    nr = [{"answerable": True, "reason": "-", "hit1": True, "gemma": False, "s_picks": -5.0, "s_top": -5.0},
          {"answerable": False, "reason": "no_item", "hit1": False, "gemma": True, "s_picks": 4.0, "s_top": 4.0}]
    assert best_theta(nr) == -5.0 and report(nr, -5.0)["combined"]["false_refusals"] == 0
    # budget: answerable non-gemma scores (combined) 0.5, 3.0
    assert budget_theta(rows, "combined", 0)["theta"] == 0.5     # refuses nothing answerable (strict <)
    assert budget_theta(rows, "combined", 1)["theta"] == 3.0     # refuses the 0.5 row only
    assert budget_theta(rows, "combined", 2)["unbounded"]
    assert budget_theta(rows, "combined_top", 0)["theta"] == 2.0  # max(0.5, 2.0)
    assert report(rows, 2.0)["combined_top"]["false_refusals"] == 0
    assert report(rows, 2.0 + 1e-6)["combined_top"]["false_refusals"] == 1
    g_rows = rows + [{"answerable": True, "reason": "-", "hit1": False, "gemma": True, "s_picks": 5.0, "s_top": 5.0}]
    assert budget_theta(g_rows, "combined", 0)["theta"] is None and budget_theta(g_rows, "alone", 0)["theta"] == 2.0
    br = budget_report(rows, 0)
    assert br["combined"]["at_theta"]["false_refusals"] == 0 and br["combined_top"]["at_theta"]["false_refusals"] == 0
    assert br["combined_top"]["at_theta"]["recall"] >= br["combined"]["at_theta"]["recall"] - 1e-9
    assert br["combined"]["auc_max_score"] == {"auc": 1.0, "n_pos": 2, "n_neg": 1}
    assert br["gemma"]["at_theta"]["refused_right"] == 1
    # with budget 0 the max-false-refusal theta of combined_top (2.0) is higher than that of combined (0.5)
    assert br["combined_top"]["theta"] > br["combined"]["theta"]
    # AUC on all scored rows vs the common subset (row 4 has no picks: only `alone` scores it)
    ab = auc_block(rows)
    assert ab["all_scored"]["alone"]["n_neg"] == 2 and ab["common_subset"]["alone"]["n_neg"] == 1
    assert ab["all_scored"]["combined"]["n_neg"] == 1

    # notes -> sets / groups
    assert note_meta(json.dumps({"set": "C1", "sub": "A"}), False) == {"set": "C1", "sub": "A", "relative": False}
    assert meta_groups(note_meta(json.dumps({"set": "C2", "sub": "P-gap", "relative": "작년"}), False)) == \
        ["C2", "C2:P-gap", "C2:relative"]
    assert note_meta("src=x year 2005->2010 sub=gap", False)["set"] == "Dv1"
    assert note_meta("", True)["set"] == "A" and note_meta("old note", False)["set"] == "B"
    assert note_meta("{bad json", True)["set"] == "A" and note_meta(None, False)["set"] == "B"

    res = [{"qid": "a", "split": "dev", "keys": ["X", "Y", "Z"], "stage2_picked_n": 1, "hit@1": 1, "no_answer_pred": False},
           {"qid": "b", "split": "dev", "keys": ["Y"], "stage2_picked_n": 0, "hit@1": 0, "no_answer_pred": True},
           {"qid": "c", "split": "test", "keys": ["X"], "stage2_picked_n": 1},
           {"qid": "e", "split": "dev", "keys": [], "stage2_picked_n": 0, "hit@1": 0, "no_answer_pred": False}]
    note = json.dumps({"set": "C1", "kind": "no_item", "sub": "A", "source": "C"})
    gold = {"a": {"question": "qa", "answerable": True, "notes": ""},
            "b": {"question": "qb", "answerable": False, "no_answer_reason": "no_item", "notes": note},
            "c": {"question": "qc"},
            "e": {"question": "qe", "answerable": False, "no_answer_reason": "no_period",
                  "notes": json.dumps({"set": "C2", "sub": "P-after"})}}
    g = gather(res, gold, "dev", 2)
    tr = [{"qid": "T1", "split": "dev", "keys": ["P", "X", "Y"], "stage2_picked_n": 1,
           "search_spans": [{"name": "rerank1", "attributes": {"top_keys": ["X", "Y", "P"]}}]},
          {"qid": "T2", "split": "dev", "keys": ["Q", "R"], "stage2_picked_n": 1}]
    tg = {"T1": {"qid": "T1", "question": "q", "answerable": True}, "T2": {"qid": "T2", "question": "q", "answerable": True}}
    g1 = gather(tr, tg, "dev", 2, "rerank1")
    assert g1[0]["top"] == ["X", "Y"] and g1[0]["picks"] == ["P"] and g1[1]["top"] == ["Q", "R"], g1
    assert gather(tr, tg, "dev", 2)[0]["top"] == ["P", "X"]
    assert [x["qid"] for x in g] == ["a", "b", "e"] and g[0]["picks"] == ["X"] and g[0]["top"] == ["X", "Y"]
    assert g[1]["picks"] == [] and g[1]["gemma"]
    assert g[0]["groups"] == ["A"] and g[1]["groups"] == ["C1", "C1:A"] and g[1]["set"] == "C1"
    score_rows(g, lambda pairs: [float(len(c)) for _, c in pairs], lambda k: k * {"X": 1, "Y": 2, "Z": 3}[k])
    assert g[0]["s_picks"] == 1.0 and g[0]["s_top"] == 2.0 and g[1]["s_picks"] is None and g[2]["s_top"] is None
    rep = report(g, 1.5)
    assert rep["combined"]["by_group"]["C1:A"]["recall"] == 1.0 and rep["combined_top"]["false_refusals"] == 0
    assert rep["combined"]["false_refusals"] == 1  # a: picked X scores 1.0 < 1.5, top Y 2.0 passes
    # pooling: the second run repeats qid a (dropped) and adds a C4 answerable row, a D ctrl and a relative C4 row
    d_row = {**g[0], "qid": "d", "set": "D", "groups": ["D", "D:ctrl"], "s_picks": -9.0, "s_top": -9.0}
    rel = {**g[0], "qid": "r", "set": "C4", "relative": True, "groups": ["C4", "C4:ctrl", "C4:relative"],
           "s_picks": -8.0, "s_top": -8.0}
    g2 = [{**g[0], "qid": "a"}, {**g[0], "qid": "f", "set": "C4", "groups": ["C4", "C4:ctrl"]}, d_row, rel]
    pooled, dup = pool([("C:/x/run1", g), ("C:/y/run1", g2)])
    assert dup == 1 and [r["qid"] for r in pooled] == ["a", "b", "e", "f", "d", "r"]
    assert pooled[3]["groups"] == ["C4", "C4:ctrl", "run:C:/y/run1"] and pooled[0]["groups"] == ["A", "run:C:/x/run1"]
    s = summarize(pooled, 1.5, "dev", 0)
    # D and relative rows are left out of the fit: theta stays 1.0 (not -9 / -8), they still show in by_group
    assert s["n_fit_answerable"] == 2 and s["n_fit_unanswerable"] == 2 and s["n_left_out_of_fit"] == 2
    assert s["dev_budget"]["combined"]["theta"] == 1.0, s["dev_budget"]["combined"]
    at = s["dev_budget"]["combined"]["at_theta"]
    assert at["false_refusals"] == 0 and at["by_group"]["D"]["false_refusals"] == 1 and at["by_group"]["C4:relative"]["n"] == 1
    assert at["by_group"]["run:C:/y/run1"]["answerable"] == 3
    assert s["no_keys_not_refused"] == {"unanswerable": 1}
    s_all = summarize(pooled, 1.5, "dev", 0, exclude_sets=(), include_relative=True)
    assert s_all["dev_budget"]["combined"]["theta"] == -9.0 and s_all["n_fit_answerable"] == 4
    assert "dev_budget" not in summarize(pooled, 1.5, "test", 0)
    assert auc([2, 3], [1]) == 1.0
    print("selftest ok")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--run", type=Path, nargs="+", help="evals/reports/<run> folder(s) with results.jsonl")
    p.add_argument("--ckpt", type=Path, help="answerability checkpoint .pt (with its .json beside it)")
    p.add_argument("--golden", nargs="*", default=["eval/golden/kos192/retrieval_test_kos192.jsonl",
                                                    "eval/golden/kos193/dev_na_plus.jsonl",
                                                    "eval/golden/kos193/dev_na_v2.jsonl"],
                   help="golden files joined by qid (missing files are skipped; add eval/golden/retrieval_golden.jsonl "
                        "for set B)")
    p.add_argument("--tables", default="data/tables.jsonl")
    p.add_argument("--split", choices=("dev", "test", "all"), default="dev")
    p.add_argument("--allow-test", action="store_true")
    p.add_argument("--theta", type=float, default=None)
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--top-source", choices=("keys", "rerank1"), default="keys",
                   help="tables for the top / alone rules: keys after stage 2, or the stage-1 re-ranker top (trace)")
    p.add_argument("--max-false-refusals", type=int, default=3,
                   help="dev budget: the reported theta per rule is the highest with at most this many false refusals")
    p.add_argument("--fit-exclude-sets", nargs="*", default=list(FIT_EXCLUDE),
                   help="sets left out of theta fitting and top-level numbers (still in by_group)")
    p.add_argument("--fit-include-relative", action="store_true", help="fit theta on relative-time rows too")
    p.add_argument("--out", type=Path, default=None, help="pooled summary file (several --run only)")
    p.add_argument("--batch", type=int, default=16)
    a = p.parse_args()
    if a.selftest:
        selftest()
        return
    if not a.run or not a.ckpt:
        raise SystemExit("need --run and --ckpt")
    if a.split != "dev" and not a.allow_test:
        raise SystemExit("--split test/all reads test questions: add --allow-test (and give --theta fixed on dev)")
    golden = {}
    for gp in a.golden:
        if Path(gp).is_file():
            for x in read_jsonl(Path(gp)):
                golden[x["qid"]] = x
        else:
            print(f"golden file not found, skipped: {gp}", file=sys.stderr)
    per_run = []
    for run in a.run:
        rows = gather(read_jsonl(run / "results.jsonl"), golden, a.split, a.top, a.top_source)
        if not rows:
            raise SystemExit(f"{run}: no results row matched the golden files and split")
        per_run.append((run, rows))
    recs = load_records(Path(a.tables))
    cache: dict[str, str] = {}

    def cards_of(k: str) -> str:
        if k not in cache:
            cache[k] = card(recs[k]) if k in recs else k
        return cache[k]

    scorer = Scorer(a.ckpt, a.batch)
    for _, rows in per_run:
        score_rows(rows, scorer, cards_of)
    theta = a.theta if a.theta is not None else scorer.meta.get("threshold_best_f1")
    if theta is None:
        raise SystemExit("no --theta and the checkpoint has no threshold_best_f1")
    head = {"ckpt": str(a.ckpt), "ckpt_meta": {k: scorer.meta.get(k) for k in ("name", "epoch", "init", "data_dir")},
            "split": a.split, "top": a.top, "top_source": a.top_source, "top_source_counts": dict(TOP_FALLBACK), "max_length": scorer.max_length, "rules": list(RULES)}
    fit_args = (a.max_false_refusals, a.fit_exclude_sets, a.fit_include_relative)
    stem = f"{a.ckpt.stem}-{a.split}"
    printed = []
    for run, rows in per_run:
        summary = {"run": str(run), **head, **summarize(rows, theta, a.split, *fit_args)}
        out_dir = run / "answerability"
        out_dir.mkdir(exist_ok=True)
        (out_dir / f"{stem}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        if a.split == "dev":
            with (out_dir / f"{stem}.scores.jsonl").open("w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps({"qid": r["qid"], "answerable": r["answerable"], "reason": r["reason"],
                                        "groups": r["groups"], "gemma": r["gemma"], "s_picks": r["s_picks"],
                                        "s_top": r["s_top"], "scores": r["scores"]}, ensure_ascii=False) + "\n")
        printed.append(summary)
    if len(per_run) > 1:
        rows, dup = pool([(str(run.resolve()), rows) for run, rows in per_run])
        summary = {"runs": [str(r) for r, _ in per_run], **head, "duplicate_qids_dropped": dup,
                   **summarize(rows, theta, a.split, *fit_args)}
        out = a.out or (per_run[0][0] / "answerability" / f"{stem}-pooled.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        summary["written_to"] = str(out)
        printed.append(summary)
    print(json.dumps(printed[0] if len(printed) == 1 else printed, ensure_ascii=False, indent=1))
    for s in printed:
        print(f"fit rows {s.get('run') or 'pooled'}: answerable {s['n_fit_answerable']}, "
              f"unanswerable {s['n_fit_unanswerable']}, left out {s['n_left_out_of_fit']}", file=sys.stderr)


if __name__ == "__main__":
    main()
