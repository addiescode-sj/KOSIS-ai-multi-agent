---
name: plan-gate
description: KOSIS 프로젝트의 모든 TASK.md 태스크 수행 절차. 구현·문서 산출물을 만들기 전에 반드시 사용 — 수행 아이디어와 3가지 옵션(성능 향상·실질 이점)을 plans/<ID>.md에 쓰고, 검증하고, 사용자 승인을 받은 뒤 구현·측정한다. "다음 태스크", "T1-05 하자", "구현해줘", "/plan-gate" 등에 사용.
---

# plan-gate: Plan → 3 Options → Verify → Decide → Implement → Measure

산출물을 바로 만들지 않는다. 승인된 plan 없이 코드/문서를 쓰면 `.claude/hooks/plan_gate.py`가 차단한다.

## 1. Pick
- 인자로 받은 ID, 없으면 TASK.md에서 의존성이 풀린 첫 `[ ]` 태스크를 고른다.
- TASK.md에서 해당 줄을 `[~]`로 바꾼다. git 브랜치 `<ID>-<slug>` (예: `T1-05-fetch-normalize`)를 만든다.
  hook은 브랜치 이름의 ID로 활성 태스크를 식별한다.
- TASK.md 상태 변경은 hook(`linear_sync.py`)이 Linear 이슈에 자동 반영한다 (TASK.md → Linear 단방향).
  Linear에서 상태를 바꾸지 말고 TASK.md를 고친다. 담당자 지정·코멘트는 Linear MCP(`linear`)로 해도 된다.

## 2. Context (읽기만)
- CLAUDE.md 원칙, 관련 기존 코드·스키마, 직전 태스크의 plan `## Result`.
- 최신 평가 리포트(`eval/reports/`)가 있으면 현재 수치를 baseline으로 가져온다. 없으면 "baseline 없음"이라고 쓴다.

## 3. Plan — `plans/<ID>.md`
[template.md](template.md)를 복사해 채운다. 핵심 규칙:
- **수행 아이디어**: 무엇을, 왜 이 순서로. 완료 기준은 TASK.md 문구를 그대로 가져온다.
- **옵션 A/B/C**: 실제로 다른 접근 3개 (같은 것의 크기만 다른 옵션 금지). 보통 최소안 / 균형안 / 고성능안.
  각 옵션에 반드시:
  - *예상 성능 향상* — 어떤 지표가 baseline 대비 얼마나 (예: 수치 정확도 72% → ~85%). 추정 근거 한 줄.
    지표가 없는 태스크(문서·골격)는 다음 단계 지표에 주는 영향으로 쓴다.
  - *실질 반영 이점* — 평가 항목(데이터 정확성·예외 처리·기술 설계 등) 중 무엇을 올리는지,
    다음 Phase에서 재사용되는지, 발표·포트폴리오에 남는 근거가 되는지.
  - *비용·리스크* — 작업 시간, LLM 비용/지연, 실패 가능성, 마감(TASK.md 날짜)과의 관계.
- **추천**: 하나를 고르고 이유. 기본값은 마감 안에 지표를 측정 가능하게 올리는 가장 단순한 옵션.

## 4. Verify
1. plan의 `## Verify` 체크리스트를 하나씩 실제로 확인하고 `[x]` 처리한다. 통과 못 하면 plan을 고친다.
2. 독립 리뷰: `Plan` 서브에이전트에 plan 파일 경로와 CLAUDE.md를 주고 다음을 요청한다 —
   "옵션들이 실제로 다른가, 성능 추정이 근거 있는가, 숫자 정확성/거절/재사용 원칙 위반이 있는가, 더 단순한 대안이 있는가. 지적만 5줄 이내."
   지적 사항과 반영 여부를 `## Verify` 아래 `리뷰:`에 기록한다.

## 5. Decide
- AskUserQuestion으로 옵션 3개를 제시한다 (추천 옵션을 첫 번째에 "(Recommended)" 표시, description에 예상 향상·비용 요약).
- 선택 결과를 `## Decision`에 기록하고 frontmatter를 `status: approved`로 바꾼다.
  **사용자 선택 없이 approved로 바꾸지 않는다.** 이것이 hook의 잠금 해제 조건이다.

## 6. Implement
- 선택한 옵션 범위만 구현한다. 범위가 바뀌면 plan을 고치고 다시 Decide.
- 비자명한 로직엔 pytest 하나 이상.

## 7. Measure & Close
- 하네스가 존재하면 실행해 baseline → after 수치를 `## Result`에 기록한다 (리포트 경로 포함).
  예상과 다르면 원인 한 줄.
- TASK.md 해당 줄을 `[x]`로 바꾸고 끝에 `→ 결과: <지표 전→후>`.
- 커밋 메시지에 태스크 ID 포함 (예: `T1-05: fetch and normalize KOSIS tables`).
