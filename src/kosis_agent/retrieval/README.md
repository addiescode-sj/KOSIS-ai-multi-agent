# retrieval/ — 검색 알고리즘 위치 지도

연구 문서 6장([KOSIS-RAG-Retrieval-Agent-Architecture.md](../../../docs/research/KOSIS-RAG-Retrieval-Agent-Architecture.md))은 `retrieval/` 아래에 검색 알고리즘 파일을 둔다. 이 레포에서 그 알고리즘은 **실전 2 submodule**(`vendor/phase2`)에 있고, 이 레포는 고치지 않고 import만 한다(CLAUDE.md). 다시 내보내기만 하는 빈 파일을 만들지 않고, 6장의 파일 이름별로 실제 위치를 아래에 적는다.

| 6장 파일 | 하는 일 | 실제 위치 |
|---|---|---|
| `dense.py` | 표 설명문 임베딩 검색 (Chroma) | [kosis_rag/retrieve/dense.py](../../../vendor/phase2/src/kosis_rag/retrieve/dense.py) |
| `sparse.py` | BM25 검색 | [kosis_rag/retrieve/bm25.py](../../../vendor/phase2/src/kosis_rag/retrieve/bm25.py) |
| (질문 색인) | 가상 질문 벡터·BM25 → 표로 묶기 (HQ) | [kosis_rag/retrieve/hq.py](../../../vendor/phase2/src/kosis_rag/retrieve/hq.py) |
| `fusion.py` | 가중 RRF로 목록 합치기 | [kosis_rag/retrieve/fuse.py](../../../vendor/phase2/src/kosis_rag/retrieve/fuse.py) · [lists.py](../../../vendor/phase2/src/kosis_rag/retrieve/lists.py) |
| `reranker.py` | 1차 크로스인코더 · 2차 LLM 선택 | [kosis_rag/rerank/stage1.py](../../../vendor/phase2/src/kosis_rag/rerank/stage1.py) · [stage2.py](../../../vendor/phase2/src/kosis_rag/rerank/stage2.py) (2차 리랭커 종류: [selectors.py](../../../vendor/phase2/src/kosis_rag/rerank/selectors.py)) |
| `catalog.py` | 표 등록부(키·이름·기간·출처) | 검색 쪽: [kosis_rag/adapters/loader.py](../../../vendor/phase2/src/kosis_rag/adapters/loader.py)의 `records` · 계산 쪽: 실전 1 [kosis_chat/api.py](../../../vendor/phase1/kosis_chat/api.py)의 `table_path()` (tables.csv 키 → Parquet) |
| (질의 해석) | 연도·주기·시도·분류·항목 읽기, 필터 조건 | [kosis_rag/parse/](../../../vendor/phase2/src/kosis_rag/parse/) |
| (판정) | found / refuse | [kosis_rag/gate/node.py](../../../vendor/phase2/src/kosis_rag/gate/node.py) |
| `ingestion/` | 표 등록부·Chroma 색인·가상 질문 만들기 (오프라인) | [kosis_rag_build/](../../../vendor/phase2/src/kosis_rag_build/) (`tables.py` · `chroma.py` · `hq/`) |

위 단계를 잇는 실전 2 그래프는 [kosis_rag/graph/search.py](../../../vendor/phase2/src/kosis_rag/graph/search.py)에 있다.

```
parse → condition → [embed] → retrieve_dense · retrieve_bm25 · [retrieve_hq] · [retrieve_hq_bm25] → fuse
      → [rerank1] → [rerank2] → gate
```

`[ ]` 단계는 설정이 있을 때만 그래프에 들어간다: `RERANK_CKPT` → rerank1, `STAGE2`(clova | hcx | gpt, 기본 none) → rerank2, `HQ_BUILD` / `HQ_BM25` → 질문 색인. 설정 이름과 기본값은 [backend/app/config.py](../../../backend/app/config.py)에 있다.

## 이 폴더에 있는 것

- [search.py](search.py): 위 그래프 전체를 `search_tables` 툴 하나로 감싼다. 메인 그래프의 `search` 노드가 이 툴을 부른다.

검색 단계를 하나씩 메인 그래프로 꺼내서 단계별로 분기하거나 재작성하는 것은 T3-23의 옵션 C로 검토했고, 이번에는 고르지 않았다([plans/T3-23.md](../../../plans/T3-23.md)). 검색이 병목이라는 측정 결과가 나오면 그때 이 폴더에 노드 래퍼를 만든다.
