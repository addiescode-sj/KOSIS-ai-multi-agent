# 답 가능 분류기 학습 코드 (Colab, v2)

Colab에서 돌리는 학습 코드 사본임. 실제 실행은 Drive `colab_project\kosis-answerability-train\`에서 함.

| 파일 | 내용 |
| --- | --- |
| `answerability_train_v2.py` | ANS2 학습 스크립트. 1차 리랭커(RR-E2s-e2) 가중치에서 시작, 질문 묶음 단위 배치, 종류별 검증 |
| `train_answerability_v2.ipynb` | Colab 노트북(1~4 준비, 5 빠른 점검, 6 본 학습 4 epoch, 7 요약) |

- 학습 데이터는 PC에서 `scripts/answerability/v2/build_data_v2.py --build ans-v2`로 만든 `ans-v2.zip`을 씀(레포에 넣지 않음).
- 실행 순서와 결과 확인은 프로젝트 안내 `06-run-guide-v2.md` 참고.
