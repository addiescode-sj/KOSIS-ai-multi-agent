"""Live OTel spans for agent runs (KOS-114, first part; T3-06): one LangChain callback the service and evals share.

Spans open while the graph runs (not replayed afterwards). Names and attributes follow the OTel GenAI semantic
conventions, so KOS-114 / KOS-115 meters and Jaeger read the same keys:

    invoke_agent             one per turn: gen_ai.conversation.id (= thread_id), kosis.turn.index
    ├─ <node>                LangGraph nodes: name == metadata langgraph_node and the parent is not a node
    │                        (same rule as evals.trace.NodeSpans, no node names hard-coded)
    ├─ execute_tool <name>   gen_ai.tool.name, gen_ai.tool.call.id, gen_ai.tool.call.arguments / .result (JSON)
    └─ chat <model>          gen_ai.request.model, gen_ai.usage.input_tokens / output_tokens

Each span's parent is the nearest enclosing run that has a span, else the turn root, so the tree holds whatever the
architecture (ReAct, Plan-and-Execute, multi-agent) nests in between.

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
    """A tool input or output as JSON text: Pydantic models dumped, a ToolMessage's artifact (else its content)."""
    if hasattr(value, "artifact") and hasattr(value, "tool_call_id"):  # ToolMessage
        value = value.artifact if value.artifact is not None else value.content
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _tokens(response) -> tuple[int, int]:
    """(input, output) tokens of an LLMResult: usage_metadata of the generated messages, else llm_output token_usage."""
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
    """Opens a real OTel span per graph node, tool call and chat model call of one turn, under `parent`."""

    def __init__(self, tracer: Tracer, parent: Span) -> None:
        self.tracer = tracer
        self.parent = parent
        self._spans: dict[UUID, Span] = {}
        self._parents: dict[UUID, UUID | None] = {}
        self._nodes: set[UUID] = set()

    def _open(self, name: str, run_id: UUID, parent_run_id: UUID | None, **attributes: Any) -> Span:
        self._parents[run_id] = parent_run_id
        anchor, up = self.parent, parent_run_id
        while up is not None:  # nearest enclosing run that has a span
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

    # graph nodes
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

    # tools
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

    # chat models
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
    """The turn's root span and the callback to pass in the run config. An exception marks the root as error."""
    with tracer.start_as_current_span(ROOT_SPAN, attributes={
            "gen_ai.operation.name": "invoke_agent", "gen_ai.conversation.id": conversation_id,
            "kosis.turn.index": index}) as root:
        yield root, LiveSpans(tracer, root)
