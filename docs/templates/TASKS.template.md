# Tasks: <title>

- plan: docs/plans/<id>/PLAN.md
- status: IN_PROGRESS | DONE

## Phase 1: Tests & Contracts

- [ ] T-001 [P] <one file, one observable result> (AC-001)
- [ ] T-002 <...> (AC-001) after: T-001
  - verify: `<command>`

## Phase 2: Implementation

- [ ] T-003 [P] <...> (FR-001)
- [ ] T-004 <...> (FR-002) after: T-003

## Phase 3: Integration & Verification

- [ ] T-005 Run full verification: `bash scripts/verify.sh` (all AC)
- [ ] T-006 Wrapup: report + current-state update

<!--
Conventions:
- [P] = parallelizable (disjoint file scope from other [P] tasks in the phase)
- after: T-xxx = dependency
- Execution appends one result line under each finished task:
  > done: <files>, <verify result>
-->
