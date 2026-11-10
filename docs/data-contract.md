# 데이터 계약: Fetch & Normalize (T3-17, KOS-143)

통계표 하나를 tidy DataFrame으로 읽는 입출력 계약이다. 계산·차트 툴과 KOS-119(2,364표 확장)가 이 계약을 쓴다.
구현은 `src/kosis_agent/services/parquet_repository.py`, 규칙 검증은 `tests/test_fetch.py`(합성 Parquet, 제공 데이터 없이 돈다)에 있다.

## 입력

```python
fetch_table(table_id, *, items=None, periods=None, prd_se=None, dims=None, data_dir=None, row_limit=ROW_LIMIT) -> TidyTable
```

| 인자 | 뜻 |
|---|---|
| `table_id` | `data/tables.csv`의 `key` (예: `101_DT_1B8000F`). 검색 결과 `TableCandidate.key`와 같다 |
| `items` | 항목 ID 목록 (`item_id`, 예: `T1`) |
| `periods` | 시점 문자열 목록 (Parquet `period` 그대로, 예: `2020`, `202301`) |
| `prd_se` | 주기 코드 목록 (`Y`·`F`·`M`·`Q`·`H` 등) |
| `dims` | `{단계 번호: [분류 코드, …]}` (예: `{1: ["11", "26"]}`) |
| `data_dir` | 제공 데이터 폴더. 비우면 `KOSIS_WORKSPACE/data` (실전 1 `kosis_ai.paths`) |
| `row_limit` | 조건을 적용한 뒤 읽을 수 있는 최대 행 수 |

오류: 없는 표 → `LookupError`. 조건을 적용해도 `row_limit`을 넘으면 → `TableTooLarge`(읽기 전에 행 수만 센다).

## 출력 `TidyTable`

- `meta`: `key`, `tbl_id`, `table_name`, `survey_name`, `organization`, `source_url`, `prd_se`(읽은 행의 주기 코드, 바꾸지 않음), `file_sha256`, `rows_in_file`
- `issues`: 이번 읽기에서 규칙이 걸린 건수 — `rows_read`, `exact_duplicates_dropped`, `ambiguous_cells`, `ambiguous_keys`, `same_value_key_rows`, `unit_unknown_cells`, `symbol_cells`
- `frame` 열:

| 열 | 뜻 |
|---|---|
| `prd_se`, `period` | 주기 코드와 시점 문자열. 원본 그대로 |
| `item_id`, `item_name` | 항목 |
| `dim{n}_name`, `dim{n}_code`, `dim{n}_label` | 분류 단계별 이름·코드·라벨. 값이 있는 단계만 남긴다 |
| `dim{n}_total` | 그 단계 라벨이 계·합계류인지 (실전 1 `is_total`) |
| `unit`, `unit_source` | 단위와 출처 (`given`·`item_name`·`dim_label`·`override`·`unknown`) |
| `value_raw` | 원본 문자열. 바꾸지 않는다 |
| `value` | 수치. `value_flag`가 `ok`·`provisional`일 때만 값이 있다 |
| `value_flag` | `ok`·`provisional`·`missing`·`suppressed`·`flagged`·`unparsed`·`ambiguous` |
| `cell_key` | `tbl|prd_se|item|코드…|period` |

## 규칙

1. **셀 키**: `cell_key`에 주기 코드를 넣는다. 월(`M`)과 분기(`Q`)가 같은 시점 문자열(예: `197001`)을 쓰기 때문이다. 실전 1 `source_ref`에는 주기 코드가 없다.
2. **중복**: 모든 원본 열(라벨·`value_raw` 포함)이 같은 행은 하나만 남긴다. 키가 같은데 `value_raw`가 다른 행은 `ambiguous`로 표시하고 `value`를 비운다. 제공 zip에서 다른 상위 항목 아래 같은 이름의 항목이 하나로 합쳐진 경우라(빌드 기록 `data/build/zip_item_map.csv`의 `dup`), **어느 숫자를 골라도 틀릴 수 있다.** 질문이 이 셀을 가리키면 거절 사유 `missing_value`로 답하고, "원본에서 같은 이름의 항목이 겹쳐 값을 하나로 정할 수 없음"을 알린다.
3. **단위**: 단위 열 → 항목명 괄호 → 분류 라벨 괄호 → 예외 키워드(합계출산율·기대수명) 순서로 찾고, 못 찾으면 `단위 미상`. 단위를 지어내지 않는다. `단위 미상` 값은 단위를 붙여 답하지 않는다.
4. **주기**: `prd_se`를 바꾸지 않는다. 5년 주기 `F`는 `F`로 남는다. 실전 1 `TableInput`은 `F`를 받지 않으므로(`PERIOD_TYPES = Y·M·Q·H`), `TableInput`으로 넘길 때의 변환은 이 계약 밖이다.
5. **결측 기호**: 비밀보호(x)·공란·대시·말줄임표·별표는 실전 1 `KIND_FLAGS` 그대로 `suppressed`·`missing`·`flagged`로 표시한다. 0이나 추정 수치로 바꾸지 않는다.
6. **계·소계**: 지우지 않고 `dim{n}_total`로 표시만 한다. 합계를 두 번 더하지 않게 고르는 일은 계산 쪽이 이 열로 한다.
7. **큰 표**: 필요한 열과 조건에 맞는 행만 읽는다(pyarrow dataset 필터). 조건을 적용한 뒤에도 `row_limit`을 넘으면 읽지 않고 `TableTooLarge`를 낸다.

## 측정

표본 200표(seed 42) 전→후는 `plans/T3-17.md`의 Result에 있다.
