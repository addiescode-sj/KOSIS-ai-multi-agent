---
name: eval-report
description: KOSIS stat 파이프라인 평가를 실행해 view.html 리포트를 만든다. run_eval → grade_run → view_run 을 순서대로 실행하고 브라우저로 연다. "eval 돌려줘", "리포트 만들어줘", "view.html 띄워줘", "/eval-report" 등에 사용. 기존 결과 폴더만 보고 싶으면 새로 실행하지 않고 view_run만 쓴다.
---

# eval-report: 실행 → 채점 → view.html

> 종합 레포: 아래 `uv run python -m eval.*` 명령 앞에 `PYTHONPATH=vendor/phase1:vendor/phase1/src`를 붙인다 (실전 1 `eval` 패키지는 submodule에 있음).

인자(선택): `--stage <이름>` (기본 `C-stat-chart`), `--backend mock|hcx` (기본 `hcx`), `--run <기존 결과 폴더>`.

## 0. 먼저 확인
- `--run`이 주어졌거나 사용자가 "새로 실행하지 말고"라고 하면 1단계를 건너뛰고 2단계부터 한다.
- `hcx`는 실제 API 호출이라 비용·시간이 든다 (40문항 × interval 5초 ≈ 수 분). 사용자가 명시적으로 요청하지 않았으면 먼저 확인한다. `mock`은 LLM을 부르지 않는다 (파이프라인 점검용, 모델 성능 아님).
- `.env`의 HCX 키는 열어 보지 않는다. 실패하면 `kosis_chat/config.py`에서 키 이름만 안내한다.

## 1. 실행 (오래 걸리므로 백그라운드)
```bash
uv run python -m eval.run_eval --stage C-stat-chart --backend hcx --model HCX-DASH-002 \
  --pipeline stat --scope oracle --serialization wide --interval 5
```
마지막 줄 `saved: eval/results/<run_id>`에서 `<run_id>` 폴더를 기록한다.
mock 점검: `--backend mock --interval 0`, `--model` 생략.

## 2. 채점 + 리포트
```bash
uv run python -m eval.grade_run --run eval/results/<run_id>
uv run python -m eval.view_run  --run eval/results/<run_id>
open eval/results/<run_id>/view.html
uv run python -m eval.regress --run eval/results/<run_id>   # 회귀 게이트: exit 1 = 회귀 있음, 2 = 비교 거부
```
regress는 view_run 뒤에 실행한다. 회귀가 있어도 view.html로 확인할 수 있게 하기 위해서다. 기준선(`eval/baselines/stat.json`)이 없으면 이 단계를 건너뛴다.
view.html은 Vega를 CDN에서 불러오므로 인터넷이 필요하다.

## 3. 사용자에게 보고할 것
- PASS/FAIL 수 (`grades/*/grades.jsonl`의 `verdict`) 와 실행 조건(backend, model, 문항 수).
- FAIL 사유 분류. 특히 '1인'·'1985년생' 같은 **라벨 속 숫자 오탐**과 **진짜 오류**를 구분한다.
- PASS인데 틀린 답 (추세 표현, 단위 중복 등)과 `fallback_chatbot` 경로로 샌 문항.
- 회귀 결과(`regress.md`): Regression 문항 수, 원인 단계, 바뀐 평가 조건.
- mock 결과면 반드시 "모델 성능이 아님"을 명시한다.

## 주의
- `eval/results/`, `logs/`는 커밋하지 않는다.
- 이 skill은 코드를 수정하지 않는다. 결과 수치를 plan/TASK.md에 기록하는 것은 `plan-gate`의 Measure 단계다.
