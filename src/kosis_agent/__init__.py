"""종합 에이전트: 실전 1·2 모듈 위의 LangGraph StateGraph. 폴더 구성은 graph/·retrieval/·services/·observability/ (CLAUDE.md §3)."""

import os
from pathlib import Path

# 실전 1 summarize_stat은 표 키를 <KOSIS_WORKSPACE>/data/tables.csv에서 찾는다. 기본값을 이 레포 루트로 둔다.
# 실전 2(kosis_rag @56b4252)는 환경변수를 읽지 않는다. 데이터 경로는 backend/app/config.py가 IndexSpec으로 넘긴다.
# 이 모듈을 import하는 것만으로 기본값이 정해지므로, 실전 1·2 모듈보다 먼저 import한다(backend/app/main.py, evals/__init__.py).
os.environ.setdefault("KOSIS_WORKSPACE", str(Path(__file__).resolve().parents[2]))
