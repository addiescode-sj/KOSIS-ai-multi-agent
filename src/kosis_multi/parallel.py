"""노드 안 병렬 실행 (비동기 A·B용, 아직 어느 에이전트도 쓰지 않음 — TODO S4).

노드는 동기 함수로 두고, 노드 안에서 스레드로 동시에 돌린다. 스레드마다 현재 contextvars를 복사해야
LangGraph config(콜백 = OTel span, stream writer)가 그 스레드에서도 보인다. 동시 상한은 HCX 429 때문에 둔다.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any


def run_parallel(fns: list[Callable[[], Any]], limit: int = 2) -> list[Any]:
    """fns를 최대 limit개씩 동시에 돌리고, 결과를 fns 순서대로 돌려준다. 예외는 그대로 올라간다."""
    if len(fns) <= 1 or limit <= 1:
        return [fn() for fn in fns]
    with ThreadPoolExecutor(max_workers=min(limit, len(fns))) as pool:
        futures = [pool.submit(contextvars.copy_context().run, fn) for fn in fns]
        return [f.result() for f in futures]
