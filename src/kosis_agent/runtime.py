"""툴이 그래프 노드 안에서 돌 때 지켜야 할 실행 문맥 규칙 두 가지 (T3-23).

1. stream_writer(): 노드 진행 이벤트를 SSE로 내보내는 LangGraph writer. 그래프 밖(단위 테스트에서 툴만 직접
   부를 때 등)에서는 None을 돌려줘서, 호출하는 쪽이 `if writer:` 한 줄로 건너뛸 수 있게 한다.
2. without_checkpointer(): 실전 1·2는 각자 compile된 LangGraph 그래프를 안에서 돌린다. 노드 안에서 그런 그래프를
   부르면 LangGraph가 부모 실행 config를 물려줘서, 부모 checkpointer가 안쪽 그래프 상태까지 저장한다
   (plans/agent-graph-refactor.md P7). scratchpad 실험으로 확인한 결과, 안쪽 상태가 `<노드>:<id>` 네임스페이스로
   체크포인트에 쌓였다. 실전 2 SearchState(Document 목록)와 실전 1 StatState를 대화마다 저장할 이유는 없으므로,
   checkpointer 키 하나만 뺀 config로 안쪽 그래프를 부른다. 콜백(OTel span)과 stream writer는 그대로 남는다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from langchain_core.runnables.config import ensure_config, var_child_runnable_config
from langgraph.config import get_stream_writer

# LangGraph가 하위 그래프에 checkpointer를 물려줄 때 쓰는 configurable 키 (langgraph 1.x의
# langgraph._internal._constants.CONFIG_KEY_CHECKPOINTER와 같은 값). 내부 모듈을 import하지 않으려고 문자열로 둔다.
# tests/test_agent_graph.py가 "안쪽 그래프 상태가 저장되지 않는다"를 확인하므로, langgraph를 올려 이름이 바뀌면 그 테스트가 깨진다.
CHECKPOINTER_KEY = "__pregel_checkpointer"


def stream_writer() -> Callable[[Any], None] | None:
    """지금 실행 중인 그래프의 custom stream writer. 그래프 밖이면 None."""
    try:
        return get_stream_writer()
    except RuntimeError:  # 그래프 밖에서 툴을 직접 부른 경우
        return None


@contextmanager
def without_checkpointer() -> Iterator[None]:
    """이 블록 안에서 부르는 그래프는 부모 checkpointer를 물려받지 않는다. 나머지 config(콜백·writer)는 그대로."""
    config = ensure_config()  # 지금 문맥의 config (그래프 밖이면 빈 기본값)
    configurable = {k: v for k, v in config.get("configurable", {}).items() if k != CHECKPOINTER_KEY}
    token = var_child_runnable_config.set({**config, "configurable": configurable})
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)  # 블록이 끝나면 원래 config로 돌려놓는다
