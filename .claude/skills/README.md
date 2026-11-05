# 프로젝트 Skill 안내

이 폴더의 skill은 KOSIS 프로젝트에서 반복되는 호출을 한 번에 실행하도록 묶어 둔 것이다.
호출은 `/<skill-name>` 이거나 관련 요청을 말하면 자동 선택된다.

## 목록

| skill | 용도 | 언제 쓰나 |
|---|---|---|
| `plan-gate` | TASK 수행 절차 (Pick → Plan → Verify → Decide → Implement → Measure) | 새 태스크 시작, 산출물을 만들기 전 (hook이 강제) |
| `eval-report` | 평가 실행 → 채점 → `view.html` 리포트 생성·열기 | 파이프라인 변경 전후 수치 확인, 팀원 PR 결과 확인 |
| `plan`, `tasks`, `execute`, `wrapup` | 계획 → 마이크로 태스크 → 서브에이전트 실행 → 결과 정리 | 큰 작업을 나눠 진행할 때 |

## 새 skill 추가 규칙

1. `.claude/skills/<name>/SKILL.md` 하나로 시작한다 (스크립트는 필요할 때만 같은 폴더에).
2. frontmatter에 `name`, `description`을 쓴다. description에 **언제 쓰는지 / 트리거 문구**를 넣어야 자동 선택된다.
3. 비용·부작용이 있는 단계(API 호출, 커밋, 푸시)는 본문에 "먼저 사용자에게 확인"을 명시한다.
4. 위 표에 한 줄 추가한다.
5. 문서는 한국어, 코드·주석은 영어.

## 공통 명령 요약 (CLAUDE.md §4 기준)

- 실전 1 평가(`eval-report`): `vendor/phase1`의 `eval` 패키지를 쓴다 → `PYTHONPATH=vendor/phase1:vendor/phase1/src uv run python -m eval.run_eval ...`
- 계층별 평가(실전 2에서 복사한 `evals/`): `PYTHONPATH=.:src:vendor/phase2/src:vendor/phase1:vendor/phase1/src uv run python -m evals.run_eval ...`. 에이전트 지표는 T3-06에서 추가
- 결과 위치: `eval/results/<run_id>/` (view.html, grades/), 로그: `logs/`
