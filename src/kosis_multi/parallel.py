"""노드 안 병렬 실행.

노드는 동기 함수로 두고, 노드 안에서 스레드로 동시에 돌린다. 쓰는 곳: ③ rerank2 ∥ 계산용 다듬기(S4b, run_beside).
①∥② 미리 검색(S4)은 기다리지 않는 작업이라 search_steps.SearchSteps.prefetch가 자기 스레드 풀로 돌린다. 스레드마다 현재 contextvars를 복사해야
LangGraph config(콜백 = OTel span, stream writer)가 그 스레드에서도 보인다.
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


def run_beside(main: Callable[[], Any], side: Callable[[], Any] | None) -> tuple[Any, Any]:
    """main은 이 스레드에서, side는 옆 스레드에서 동시에 돌린다. (main 결과, side 결과). side가 None이면 (main(), None).
    main이 예외를 내면 side를 기다린 뒤 그 예외를 올린다. side의 예외도 그대로 올라간다(side가 스스로 잡아야 함)."""
    if side is None:
        return main(), None
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(contextvars.copy_context().run, side)
        result = main()
        return result, future.result()
