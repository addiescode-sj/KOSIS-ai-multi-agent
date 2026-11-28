# Plan: e2e golden label review

- id: 20261121-e2e-golden-review
- status: APPROVED
- date: 2026-11-21

## Intent

Fill the `verdict(ok/wrong)` and `note` fields for all 42 rows in `evals/datasets/e2e_golden.review.csv` through an independent review of the constructed gold labels. Record enough reasoning to make each decision auditable.

## Problem / Why

The review sheet has 42 blank verdicts, including 18 rows marked `needs_review` because rule predictions differ from gold. A disagreement alone does not establish that gold is wrong; the source scenario, question, table metadata, and cells must determine the verdict.

## Requirements

- FR-001: Assign `ok` or `wrong` to each of the 42 rows. Judge whether the **gold** label is correct, not whether the rule prediction matches it.
- FR-002: Write a concise row-specific note explaining the decision. For `wrong`, identify the corrected label or the precise defect and supporting evidence.
- FR-003: Preserve row order and the seven source columns (`sid` through `review`) exactly; edit only `verdict(ok/wrong)` and `note`.
- NFR-001: Do not read or modify `.env`, `secrets/**`, `*.pem`, or `*.key`. Keep code and comments in English.

## Acceptance Criteria

- AC-001: CSV parses as UTF-8 with BOM; it retains 42 unique `sid` values and the original header and row order.
- AC-002: Every verdict is exactly `ok` or `wrong`, and every note is nonempty and supported by the scenario and data.
- AC-003: A comparison against the pre-edit copy confirms only the two review columns changed.
- AC-004: `bash scripts/verify.sh` exits 0, with results recorded in `docs/reports/20261121-e2e-golden-review-REPORT.md`; `docs/current-state.md` records the review outcome.

## Technical Decisions

- D-001: Use `e2e_golden.jsonl`, the builder, and local table data to independently check each question and gold label. The `rule_pred` field is diagnostic evidence only.
  - Rationale: The review measures gold-label quality and includes both agreement samples and disagreements.
  - Linked: FR-001, FR-002
- D-002: Keep this task to annotation. If a gold label is wrong, record the correction in `note` and leave dataset regeneration or rule changes to a separate decision.
  - Rationale: The request names the review CSV fields only.
  - Linked: FR-002, FR-003

## Scope

### In scope

- `evals/datasets/e2e_golden.review.csv`
- Plan, task, report, and current-state files required by the repository workflow

### Non-goals

- Changing golden scenarios, generation rules, runtime behavior, or test definitions

## Risks

- A generated question can be ambiguous despite a valid cell. Review table dimensions and scenario expectations before marking `ok`; explain ambiguity in the note when marking `wrong`.
- The CSV is generated and may be overwritten by a future builder run. Record the reviewed source checksum in the report.

## Open Questions

- None.

## Decision

2026-11-21: User explicitly approved this plan.
