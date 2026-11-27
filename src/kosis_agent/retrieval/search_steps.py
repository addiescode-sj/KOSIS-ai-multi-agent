"""실전 2 검색을 두 몫으로 나눠 돈다 (T3-60, T3-09 실험 S3·S4에서 옮김). phase2 submodule은 고치지 않는다.

    candidates 노드: parse → condition → [embed] → 목록들 → fuse → [rerank1]   — LLM 없음, 후보만
    search 노드:     search_tables 툴 = [rerank2(Qwen)] → gate → to_output      — 고르기·거절

phase2 graph/search.graph_nodes의 노드(이름·함수)를 그대로 써서 작은 그래프 둘로 묶는다. 연결은 add_search_edges와 같다
(목록 dense·BM25·질문 색인은 병렬, fuse가 모두 기다림). 그래서 둘을 이어 돌면 run_search와 같은 결과이고, 검색 노드
trace 항목(SSE 노드 이벤트)과 SearchOutput.spans도 기존과 같다. 달라지는 것은 후보 몫의 노드가 search_tables 툴 span 밖
(candidates 노드 또는 미리 검색이면 understand 노드 아래)에서 돈다는 점이다. evals의 retrieve_ms는 툴 span 시간만
더하므로 검색 시간에서 후보 몫이 빠진다. 지연 비교는 턴 전체 시간으로 한다.

후보 몫의 중간 상태는 보관함에 (대화 thread_id, 질문)으로 둔다. search_tables 툴이 그것을 꺼내 rerank2·gate를 마저
돈다. 그래서 툴 인자는 기존처럼 question 하나이고, 툴 결과도 기존과 같은 SearchOutput이다. 보관함에 없으면(후보 몫 없이
툴만 부른 경우: multi 노드) 툴이 처음부터 다 돈다.

미리 검색: understand가 판단(Qwen)을 시작할 때 prefetch(원문)가 옆 스레드에서 후보 몫을 미리 돌린다. candidates 노드가
같은 질문이면 그 결과를 기다려 쓰고(다시 찾지 않음), 다른 질문(재작성·이월)이면 다시 찾는다. 쓰이지 않은 미리 검색은
finalize에서 끝나기를 기다렸다 버린다(검색 span이 다음 턴 기록으로 넘어가지 않게). 미리 검색은 stream 이벤트를 바로
내지 않고 candidates 노드가 쓸 때 한꺼번에 낸다(턴이 끝난 뒤 writer를 부르지 않게). 툴 span이 없어서 쓰지 않아도
extra_call이 생기지 않는다.

검색 모듈이 SearchModule이 아니면(테스트 가짜: stream만 있음) 나누지 않고 search.stream 전체를 툴에서 한 번 돈다.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from langgraph.graph import END, START, StateGraph

from kosis_agent.retrieval.search import stream_search
from kosis_agent.runtime import stream_writer, without_checkpointer
from kosis_rag.api import to_output
from kosis_rag.graph.search import graph_nodes
from kosis_rag.schemas.io import SearchOutput
from kosis_rag.schemas.state import SearchState

log = logging.getLogger("kosis.search.steps")
KEEP = 64  # 보관함 크기: 꺼내지 않은 중간 상태가 쌓이지 않게


def thread_of(config: dict | None) -> str:
    return str(((config or {}).get("configurable") or {}).get("thread_id") or "")


def split_graphs(nodes: dict) -> tuple[Any, Any]:
    """graph_nodes를 후보 그래프(parse → condition → [embed] → 목록들 → fuse → [rerank1])와
    고르기 그래프([rerank2] → gate)로 나눠 compile한다. 연결 규칙은 phase2 add_search_edges와 같다."""
    branches = [name for name in nodes if name.startswith("retrieve_")]
    fan_out = "embed" if "embed" in nodes else "condition"

    a = StateGraph(SearchState)
    for name in ("parse", "condition", "embed", *branches, "fuse", "rerank1"):
        if name in nodes:
            a.add_node(name, nodes[name])
    a.add_edge(START, "parse")
    a.add_edge("parse", "condition")
    if fan_out == "embed":
        a.add_edge("condition", "embed")
    for name in branches:
        a.add_edge(fan_out, name)
    a.add_edge(branches, "fuse")
    last = "rerank1" if "rerank1" in nodes else "fuse"
    if last == "rerank1":
        a.add_edge("fuse", "rerank1")
    a.add_edge(last, END)

    b = StateGraph(SearchState)
    line = [name for name in ("rerank2", "gate") if name in nodes]
    for name in line:
        b.add_node(name, nodes[name])
    for x, y in zip([START, *line], [*line, END]):
        b.add_edge(x, y)
    return a.compile(), b.compile()


class SearchSteps:
    """검색 모듈 하나를 후보·고르기 몫으로 나눠 부르는 어댑터. build_graph가 한 번 만들어 노드·툴에 넘긴다."""

    def __init__(self, search: Any):
        self.search = search
        resources = getattr(search, "resources", None)
        self.split = resources is not None and callable(getattr(search, "k", None))
        if self.split:
            self.k = search.k(None)
            self.candidates_graph, self.pick_graph = split_graphs(graph_nodes(resources, k=self.k))
        self._pending: OrderedDict[tuple[str, str], SearchState] = OrderedDict()
        self._prefetched: dict[tuple[str, str], Future] = {}
        self._pool: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 안쪽 실행

    def _run(self, graph: Any, state: SearchState, emit: bool = True) -> SearchState:
        """작은 검색 그래프 하나를 돈다. 노드가 끝날 때마다 (이름, trace 항목)을 흘려보낸다(기존 stream_search와 같음).
        emit=False(미리 검색)면 흘려보내지 않는다."""
        writer = stream_writer() if emit else None
        final: dict = {}
        inp = {name: getattr(state, name) for name in SearchState.model_fields}
        with without_checkpointer():
            for mode, chunk in graph.stream(inp, stream_mode=["updates", "values"]):
                if mode == "values":
                    final = chunk
                elif writer:
                    for name, update in chunk.items():
                        writer((name, update["trace"][name]))
        return SearchState.model_validate(final)

    # ------------------------------------------------------------ 후보 · 고르기

    def prefetch(self, question: str, config: dict | None = None) -> None:
        """understand 판단과 동시에 후보 몫을 옆 스레드에서 미리 돌린다(기다리지 않음). 콜백(span)은 지금 노드 아래로 열린다."""
        if not self.split:
            return
        with self._lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="kosis-prefetch")
            run = contextvars.copy_context().run
            self._prefetched[(thread_of(config), question)] = self._pool.submit(
                run, self._run, self.candidates_graph, SearchState(question=question), False)

    def _take_prefetched(self, question: str, config: dict | None) -> SearchState | None:
        with self._lock:
            future = self._prefetched.pop((thread_of(config), question), None)
        if future is None:
            return None
        try:
            state = future.result()
        except Exception as error:  # noqa: BLE001 - 미리 검색 실패: candidates 노드가 다시 찾는다
            log.warning("prefetch failed, searching again: %s: %s", type(error).__name__, error)
            return None
        if writer := stream_writer():  # 미리 검색이 미뤄 둔 진행 이벤트를 이제 낸다
            for name, entry in state.trace.items():
                writer((name, entry))
        return state

    def settle(self, config: dict | None = None) -> None:
        """턴 끝: 이 대화에서 쓰이지 않은 미리 검색이 끝나기를 기다렸다 버린다."""
        thread = thread_of(config)
        with self._lock:
            keys = [key for key in self._prefetched if key[0] == thread]
            futures = [self._prefetched.pop(key) for key in keys]
        for future in futures:
            try:
                future.result()
            except Exception:  # noqa: BLE001 - 버리는 결과
                pass

    def retrieve_candidates(self, question: str, config: dict | None = None) -> None:
        """후보 몫(candidates 노드): 후보까지 돌려 보관함에 둔다. 같은 질문을 미리 찾아 뒀으면 그것을 기다려 쓴다.
        나누지 않는 모듈이면 아무것도 하지 않는다(search_tables 툴이 전체를 돎)."""
        if not self.split:
            return
        state = self._take_prefetched(question, config)
        if state is None:
            state = self._run(self.candidates_graph, SearchState(question=question))
        with self._lock:
            self._pending[(thread_of(config), question)] = state
            while len(self._pending) > KEEP:
                self._pending.popitem(last=False)

    def take(self, question: str, config: dict | None = None) -> SearchState | None:
        with self._lock:
            return self._pending.pop((thread_of(config), question), None)

    def rerank_and_gate(self, question: str, config: dict | None = None) -> SearchOutput:
        """고르기 몫(search_tables 툴 안): 보관한 후보에 rerank2 → gate → to_output. 후보가 없으면 처음부터 돈다."""
        if not self.split:
            return stream_search(self.search, question)
        state = self.take(question, config)
        if state is None:
            state = self._run(self.candidates_graph, SearchState(question=question))
        return to_output(self._run(self.pick_graph, state), self.k)
