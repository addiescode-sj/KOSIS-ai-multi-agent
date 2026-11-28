# Tasks: e2e golden label review

- id: 20261121-e2e-golden-review
- plan: `docs/plans/20261121-e2e-golden-review/PLAN.md`

## Tasks

- [x] T-001: Independently inspect all 42 review rows against scenario expectations, source metadata, and available cells. Record row-specific decisions and evidence. Read-only investigation; no source changes.
- [x] T-002: Edit only `verdict(ok/wrong)` and `note` in `evals/datasets/e2e_golden.review.csv`. Preserve BOM, source columns, header, and row order. Check that all 42 rows are complete and only those two columns changed.
- [x] T-003: Run `bash scripts/verify.sh`; review the diff against the plan and record verification evidence.
- [x] T-004: Write `docs/reports/20261121-e2e-golden-review-REPORT.md` and update `docs/current-state.md` with the results and source checksum.
