"""L0 + L1 filter (gold and pred slots) + L2 in one run folder. ops and ops_history stay per part (run_eval)."""

from __future__ import annotations

import argparse

from evals.tasks import ablation, l0_metadata, l2_query


def run(args) -> tuple[list[dict], dict, str]:
    """L0 + L1 filter (gold and pred slots) + L2 in one run folder; rows carry `layer`, summary and ops stay per layer."""
    with_source = lambda src: argparse.Namespace(**{**vars(args), "slot_source": src})  # noqa: E731
    parts = {"l0": l0_metadata.run(args), "l1-gold": ablation.run(with_source("gold")), "l1-pred": ablation.run(with_source("pred")),
             "l2": l2_query.run(args)}
    # run() skips questions without a slot reading; gold reads query_golden, pred reads the retrieval golden set
    gold, pred = ({(r["combo"], r["qid"]) for r in parts[p][0]} for p in ("l1-gold", "l1-pred"))
    assert gold == pred, f"gold and pred slot runs cover different questions: {sorted(gold ^ pred)[:5]}"
    rows = [{**r, "layer": name} for name, (part, _, _) in parts.items() for r in part]
    report = "\n".join(f"<!-- {name} -->\n{rep}" for name, (_, _, rep) in parts.items())
    return rows, {name: s for name, (_, s, _) in parts.items()}, report
