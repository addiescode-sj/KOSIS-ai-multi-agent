"""에이전트 실행의 실시간 OTel span (KOS-114 1부, T3-06; T3-23에서 observability/로 옮김).

서비스와 평가가 함께 쓰는 LangChain 콜백 하나다. span은 그래프가 도는 동안 열린다(끝난 뒤 다시 그리지 않음).
이름과 속성은 OTel GenAI 시맨틱 규약을 따라서, KOS-114 / KOS-115 지표와 Jaeger가 같은 키를 읽는다.

    invoke_agent             턴마다 하나: gen_ai.conversation.id (= thread_id), kosis.turn.index
    ├─ <node>                LangGraph 노드: 이름 == metadata langgraph_node 이고 부모가 노드가 아닌 것
    │                        (evals.trace.NodeSpans와 같은 규칙, 노드 이름을 하드코딩하지 않음)
    ├─ execute_tool <name>   gen_ai.tool.name, gen_ai.tool.call.id, gen_ai.tool.call.arguments / .result (JSON)
    └─ chat <model>          gen_ai.request.model, gen_ai.usage.input_tokens / output_tokens

각 span의 부모는 span을 가진 가장 가까운 상위 실행이고, 없으면 턴 루트다. 그래서 아키텍처(워크플로, ReAct,
Plan-and-Execute, 멀티 에이전트)가 중간에 무엇을 끼워 넣든 트리가 유지된다.

    tracer = provider.get_tracer("kosis-agent")
    with agent_turn(tracer, thread_id, index) as (root, spans):
        graph.invoke(inputs, config={"configurable": {"thread_id": thread_id}, "callbacks": [spans]})
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode, Tracer

ROOT_SPAN = "invoke_agent"
TOOL_PREFIX = "execute_tool "
CHAT_PREFIX = "chat "


def to_json(value: Any) -> str:
    """툴 입력·출력을 JSON 텍스트로 바꾼다. Pydantic 모델은 dump하고, ToolMessage는 content를 쓴다.

    T3-23: ToolMessage에서 artifact가 아니라 content를 기록한다. 이 레포 툴은 content에 채점 대상(SearchOutput /
    StatAnswer JSON)을, artifact에 노드만 쓰는 큰 객체(StatOutput: 차트·spans 포함)를 둔다. content는 LLM이
    ToolMessage로 읽는 내용이기도 하다. 그래서 하네스(evals/evaluators/agent.py)가 채점하는 값과 에이전트가 본 값이
    같아진다. 툴을 ToolCall 형태로 부르면(워크플로 노드의 call_tool, ReAct의 ToolNode) on_tool_end에 ToolMessage가 온다.
    """
    if hasattr(value, "artifact") and hasattr(value, "tool_call_id"):  # ToolMessage
        value = value.content
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _tokens(response) -> tuple[int, int]:
    """LLMResult의 (입력, 출력) 토큰: 생성 메시지의 usage_metadata, 없으면 llm_output의 token_usage."""
    tin = tout = 0
    for gens in response.generations:
        for g in gens:
            usage = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
            tin, tout = tin + int(usage.get("input_tokens") or 0), tout + int(usage.get("output_tokens") or 0)
    if not (tin or tout):
        usage = (response.llm_output or {}).get("token_usage") or {}
        tin, tout = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    return tin, tout


class LiveSpans(BaseCallbackHandler):
    """한 턴의 그래프 노드·툴 호출·chat model 호출마다 실제 OTel span을 `parent` 아래에 연다."""

    def __init__(self, tracer: Tracer, parent: Span) -> None:
        self.tracer = tracer
        self.parent = parent
        self._spans: dict[UUID, Span] = {}
        self._parents: dict[UUID, UUID | None] = {}
        self._nodes: set[UUID] = set()

    def _open(self, name: str, run_id: UUID, parent_run_id: UUID | None, **attributes: Any) -> Span:
        self._parents[run_id] = parent_run_id
        anchor, up = self.parent, parent_run_id
        while up is not None:  # span을 가진 가장 가까운 상위 실행을 찾는다
            if up in self._spans:
                anchor = self._spans[up]
                break
            up = self._parents.get(up)
        span = self.tracer.start_span(name, context=trace.set_span_in_context(anchor),
                                      attributes={k: v for k, v in attributes.items() if v is not None})
        self._spans[run_id] = span
        return span

    def _end(self, run_id: UUID, error: BaseException | None = None, **attributes: Any) -> None:
        span = self._spans.pop(run_id, None)
        if span is None:
            return
        span.set_attributes({k: v for k, v in attributes.items() if v is not None})
        if error is not None:
            span.record_exception(error)
            span.set_status(Status(StatusCode.ERROR, f"{type(error).__name__}: {error}"))
        span.end()

    # 그래프 노드
    def on_chain_start(self, serialized, inputs, *, run_id: UUID, parent_run_id: UUID | None = None,
                       metadata: dict | None = None, **kwargs: Any) -> None:
        node = (metadata or {}).get("langgraph_node")
        if node is None or kwargs.get("name") != node or parent_run_id in self._nodes:
            self._parents[run_id] = parent_run_id
            return
        self._nodes.add(run_id)
        self._open(node, run_id, parent_run_id)

    def on_chain_end(self, outputs, *, run_id: UUID, **kwargs: Any) -> None:
        self._end(run_id)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._end(run_id, error)

    # 툴
    def on_tool_start(self, serialized, input_str: str, *, run_id: UUID, parent_run_id: UUID | None = None,
                      inputs: dict | None = None, **kwargs: Any) -> None:
        name = kwargs.get("name") or (serialized or {}).get("name") or "tool"
        self._open(TOOL_PREFIX + name, run_id, parent_run_id, **{
            "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name,
            "gen_ai.tool.call.id": kwargs.get("tool_call_id"),
            "gen_ai.tool.call.arguments": to_json(inputs) if inputs is not None else input_str})

    def on_tool_end(self, output, *, run_id: UUID, **kwargs: Any) -> None:
        self._end(run_id, **{"gen_ai.tool.call.result": to_json(output)})

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._end(run_id, error)

    # chat model
    def on_chat_model_start(self, serialized, messages, *, run_id: UUID, parent_run_id: UUID | None = None,
                            metadata: dict | None = None, **kwargs: Any) -> None:
        params = kwargs.get("invocation_params") or {}
        model = params.get("model") or params.get("model_name") or (metadata or {}).get("ls_model_name") or "unknown"
        self._open(CHAT_PREFIX + model, run_id, parent_run_id,
                   **{"gen_ai.operation.name": "chat", "gen_ai.request.model": model})

    def on_llm_end(self, response, *, run_id: UUID, **kwargs: Any) -> None:
        tin, tout = _tokens(response)
        self._end(run_id, **{"gen_ai.usage.input_tokens": tin, "gen_ai.usage.output_tokens": tout})

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._end(run_id, error)


@contextmanager
def agent_turn(tracer: Tracer, conversation_id: str, index: int) -> Iterator[tuple[Span, LiveSpans]]:
    """턴의 루트 span과, 실행 config에 넣을 콜백. 블록 안에서 예외가 나면 루트 span을 error로 표시한다."""
    with tracer.start_as_current_span(ROOT_SPAN, attributes={
            "gen_ai.operation.name": "invoke_agent", "gen_ai.conversation.id": conversation_id,
            "kosis.turn.index": index}) as root:
        yield root, LiveSpans(tracer, root)
