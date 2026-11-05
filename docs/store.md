# 관계형 저장소: PostgreSQL + SQLAlchemy (T3-18, KOS-86)

대화 기록과 평가 실행 집계를 저장한다. 모델은 `src/kosis_store/models.py`, 엔진·세션은 `src/kosis_store/db.py`,
테이블 생성·변경은 Alembic(`migrations/`)이 한다. API·대화 상태 연결은 T3-04에서 한다.

## 테이블

| 테이블 | 한 행 | 주요 열 |
|---|---|---|
| `conversations` | 대화 하나 | `id`(임의 UUID), `created_at` |
| `messages` | 한 턴의 질문 또는 답 | `conversation_id`(대화 삭제 시 함께 삭제), `turn`, `role`(user·assistant), `content`, `result`(`SearchResult` JSON), `created_at`. `(conversation_id, turn, role)`은 하나만 |
| `eval_runs` | 평가 실행 1회 | `evals/reports/ops_history.jsonl` 한 줄의 열 그대로(`run_id`, `layer`, `stage`, `target`, 지연 p50·p95, 토큰, 비용, 실패율) |

## 개인정보·보안

- `messages.content`·`result`에는 **사용자 질문 원문이 그대로 저장된다.** 사용자 식별자는 저장하지 않고, 대화는 임의 UUID로만 묶는다.
- 보관 기간(계획서 초안 30일, 팀 확정 전)이 지나면 `conversations.created_at` 기준으로 대화를 지운다. 메시지는 함께 지워진다(`ON DELETE CASCADE`). 삭제 작업은 아직 없다.
- `eval_runs`에는 집계만 둔다. 골든 질문 문장·정답·문항 ID는 넣지 않는다(보안 서약).
- compose `postgres`는 `127.0.0.1:5432`에만 열린다. 기본 계정 `kosis/kosis`는 로컬 개발용이다. 배포(M6)에서는 시크릿으로 주입한다.

## 실행

```bash
docker compose up -d postgres
```

```bash
DATABASE_URL=postgresql+psycopg://kosis:kosis@localhost:5432/kosis PYTHONPATH=src alembic upgrade head
```

- compose 안의 backend는 `DATABASE_URL`의 호스트가 `postgres`다(`docker-compose.yml`).
- 테이블을 바꿀 때는 모델을 고치고 `migrations/versions/`에 다음 번호(`0002_…`)를 더한다. `create_all`은 오프라인 테스트(SQLite)에서만 쓴다.

## 테스트

- 오프라인(`pytest`, CI): SQLite로 쓰기·읽기, 턴·역할 중복 금지, 대화 삭제 시 메시지 삭제, 평가 테이블에 골든 열이 없음을 확인한다.
- PostgreSQL(`-m postgres`): `TEST_DATABASE_URL`(버리는 DB, 예: `kosis_test`)이 있을 때만 돈다. `alembic upgrade head` → 모델과 마이그레이션 일치(`compare_metadata`) → 쓰기·읽기·DB 수준 연쇄 삭제 → `downgrade base`. backend의 `DATABASE_URL`은 쓰지 않는다.

```bash
docker compose exec -T postgres psql -U kosis -d kosis -c "CREATE DATABASE kosis_test"
```

```bash
TEST_DATABASE_URL=postgresql+psycopg://kosis:kosis@localhost:5432/kosis_test python -m pytest tests/test_store.py
```
