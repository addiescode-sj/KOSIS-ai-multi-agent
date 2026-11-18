"""CI quality gate (plans/T3-19.md): the newest committed ragas record must not drop below the committed baseline.

Runs offline in scripts/verify.sh (and so in CI) on two committed files: evals/reports/quality_history.jsonl (one
aggregate line per ragas run) and evals/quality_baseline.json. Only records with the baseline's comparison key count,
so an experiment on another graph or model never fails it. To accept a new level on purpose:
`python -m evals.quality --promote evals/reports/<ragas run>` and commit the baseline change.
"""

import pytest

from evals import quality

RECORD, ROWS = quality.check()


@pytest.mark.parametrize("row", ROWS, ids=[r["metric"] for r in ROWS])
def test_no_quality_drop_against_baseline(row):
    latest = "missing" if row["latest"] is None else f"{row['latest']:.3f}"
    assert row["ok"], (f"{row['metric']} dropped {row['baseline']:.3f} -> {latest} (tolerance {row['tolerance']:.3f}) "
                       f"in {RECORD['run_id']}; fix the regression or promote a new baseline on purpose")


def test_gate_has_something_to_compare():
    if not quality.BASELINE.exists():
        pytest.skip("no evals/quality_baseline.json yet")
    assert RECORD is not None, "quality_history.jsonl has no record with the baseline's key"
