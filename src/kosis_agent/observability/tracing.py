"""에이전트 실행의 실시간 OTel span (KOS-114 1부, T3-06; T3-23에서 observability/로 옮김).

서비스와 평가가 함께 쓰는 LangChain 콜백 하나다. span은 그래프가 도는 동안 열린다(끝난 뒤 다시 그리지 않음).
이름과 속성은 OTel GenAI 시맨틱 규약을 따라서, KOS-114 / KOS-115 지표와 Jaeger가 같은 키를 읽는다.

    invoke_agent             턴마다 하나: gen_ai.conversation.id (= thread_id), kosis.turn.index
    ├─ <node>                LangGraph 노드: 이름 == metadata langgraph_node 이고 부모가 노드가 아닌 것
    │                        (evals.trace.NodeSpans와 같은 규칙, 노드 이름을 하드코딩하지 않음)
    ├─ execute_tool <name>   gen_ai.tool.name, gen_ai.tool.call.id, gen_ai.tool.call.arguments / .result (JSON)
    └─ chat <model>          gen_ai.request.model, gen_ai.usage.input_tokens / output_tokens

LLM 호출 = `gen_ai.operation.name == "chat"`인 span (T3-31, KOS-115). LangChain이 못 보는 두 곳은 툴 기록에서 만든다:
- 실전 1 HCX 단계(urllib): StatOutput.spans 중 gen_ai.request.model이 있는 단계 (이름은 실전 1 단계 이름 그대로)
- 실전 2 rerank2(CLOVA reranker / HCX, urllib): SearchOutput.spans의 rerank2 항목 → `chat <model>`
세 경로 모두 _llm_done을 지나며 OTel 메트릭 gen_ai.client.token.usage · gen_ai.client.operation.duration을 남긴다.
LLM 응답의 종료 이유는 chat span의 gen_ai.response.finish_reasons(LangChain만, 응답에 있을 때), 실패는 error.type
(실전 1은 llm.status, rerank2는 stage2_error).

턴 종료 이유 (T3-31): 그래프 최상위 실행이 끝나면 루트 span에 kosis.outcome(answered | refused | clarify | failed,
예외면 error) · kosis.no_answer_reason(ChatResult와 같은 값) · error.type을 붙이고, Counter kosis.agent.turns를 같은
속성으로 1 올린다. 정상 종료와 거절·실패·예외를 Jaeger 검색과 메트릭 집계 양쪽에서 같은 키로 본다.

각 span의 부모는 span을 가진 가장 가까운 상위 실행이고, 없으면 턴 루트다. 그래서 아키텍처(워크플로, ReAct,
Plan-and-Execute, 멀티 에이전트)가 중간에 무엇을 끼워 넣든 트리가 유지된다.

    tracer = provider.get_tracer("kosis-agent")
    with agent_turn(tracer, thread_id, index) as (root, spans):
        graph.invoke(inputs, config={"configurable": {"thread_id": thread_id}, "callbacks": [spans]})
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Iterator
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from opentelemetry import metrics, trace
from opentelemetry.metrics import Meter
from opentelemetry.trace import Span, Status, StatusCode, Tracer

ROOT_SPAN = "invoke_agent"
TOOL_PREFIX = "execute_tool "
CHAT_PREFIX = "chat "
CHAT = "chat"  # gen_ai.operation.name of every LLM call span (평가 ops가 이 값으로 LLM 호출을 센다)
STAGE2_MODEL = "kosis.stage2_model"  # search_tables 툴 metadata: 실전 2 stage 2 LLM 모델 이름 (trace에는 없음)


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


def _finish_reasons(response) -> list[str] | None:
    """LLMResult의 종료 이유 (stop | length | content_filter | tool_calls ...): generation_info, 없으면 response_metadata."""
    reasons = []
    for gens in response.generations:
        for g in gens:
            meta = {**(getattr(getattr(g, "message", None), "response_metadata", None) or {}), **(g.generation_info or {})}
            if reason := meta.get("finish_reason") or meta.get("finishReason"):
                reasons.append(str(reason))
    return reasons or None


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

    def __init__(self, tracer: Tracer, parent: Span, meter: Meter | None = None) -> None:
        self.tracer = tracer
        self.parent = parent
        self._spans: dict[UUID, Span] = {}
        self._parents: dict[UUID, UUID | None] = {}
        self._nodes: set[UUID] = set()
        self._started: dict[UUID, tuple[str, float]] = {}  # chat model run -> (model, perf_counter) (메트릭)
        self._models: dict[UUID, str] = {}  # tool run -> stage 2 model (search_tables metadata)
        self._top: UUID | None = None  # 그래프 최상위 실행 (부모 없는 첫 chain) = 이 턴
        meter = meter or metrics.NoOpMeter("kosis-agent")
        self._token_usage = meter.create_histogram(
            "gen_ai.client.token.usage", unit="{token}", description="Number of input and output tokens used")
        self._duration = meter.create_histogram(
            "gen_ai.client.operation.duration", unit="s", description="GenAI operation duration")
        self._turns = meter.create_counter(
            "kosis.agent.turns", unit="{turn}", description="Agent turns by how they ended (outcome, reason, error)")

    def _turn_done(self, outputs: Any = None, error: BaseException | None = None) -> None:
        """턴 종료 이유를 루트 span 속성과 Counter에 남긴다. 이유는 ChatResult(chat_result)와 같은 규칙으로 읽는다."""
        attributes: dict[str, Any] = {}
        if error is not None:
            attributes = {"kosis.outcome": "error", "error.type": type(error).__name__}
        elif isinstance(outputs, dict) and outputs.get("outcome"):
            attributes = {"kosis.outcome": outputs["outcome"]}
            try:
                from kosis_agent.graph.state import chat_result  # 지연 import: graph → retrieval → 이 모듈 순환을 피한다

                reason = chat_result(outputs).no_answer_reason
            except Exception:  # noqa: BLE001 - 관측이 답을 깨면 안 된다 (search 없는 턴 등): outcome만 남긴다
                reason = None
            if reason:
                attributes["kosis.no_answer_reason"] = reason
        if not attributes:  # 이 레포 워크플로가 아닌 그래프(outcome 없음): 남길 것이 없다
            return
        self.parent.set_attributes(attributes)
        self._turns.add(1, attributes)

    def _llm_done(self, model: str, tin: int | None, tout: int | None, seconds: float,
                  error: str | None = None) -> None:
        """LLM 호출 하나의 메트릭 (OTel GenAI 규약 이름). 토큰을 모르면(None) 그 토큰 종류는 기록하지 않는다."""
        attributes = {"gen_ai.operation.name": CHAT, "gen_ai.request.model": model}
        self._duration.record(seconds, {**attributes, "error.type": error} if error else attributes)
        for kind, n in (("input", tin), ("output", tout)):
            if n is not None:
                self._token_usage.record(int(n), {**attributes, "gen_ai.token.type": kind})

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
        if parent_run_id is None and self._top is None:
            self._top = run_id
        node = (metadata or {}).get("langgraph_node")
        if node is None or kwargs.get("name") != node or parent_run_id in self._nodes:
            self._parents[run_id] = parent_run_id
            return
        self._nodes.add(run_id)
        self._open(node, run_id, parent_run_id)

    def on_chain_end(self, outputs, *, run_id: UUID, **kwargs: Any) -> None:
        if run_id == self._top:
            self._turn_done(outputs)
        self._end(run_id)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        if run_id == self._top:
            self._turn_done(error=error)
        self._end(run_id, error)

    # 툴
    def on_tool_start(self, serialized, input_str: str, *, run_id: UUID, parent_run_id: UUID | None = None,
                      inputs: dict | None = None, **kwargs: Any) -> None:
        name = kwargs.get("name") or (serialized or {}).get("name") or "tool"
        if (model := (kwargs.get("metadata") or {}).get(STAGE2_MODEL)) is not None:
            self._models[run_id] = model
        self._open(TOOL_PREFIX + name, run_id, parent_run_id, **{
            "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name,
            "gen_ai.tool.call.id": kwargs.get("tool_call_id"),
            "gen_ai.tool.call.arguments": to_json(inputs) if inputs is not None else input_str})

    def on_tool_end(self, output, *, run_id: UUID, **kwargs: Any) -> None:
        span = self._spans.get(run_id)
        if span is not None:
            steps = getattr(getattr(output, "artifact", None), "spans", None)
            self._step_spans(span, steps)
            self._stage2_span(span, steps, self._models.pop(run_id, None))
        self._end(run_id, **{"gen_ai.tool.call.result": to_json(output)})

    def _step_spans(self, tool_span: Span, steps: list[dict] | None) -> None:
        """툴 안쪽 단계 기록(실전 1 StatOutput.spans: name·start_ms·duration_ms·status·attributes)을 툴 span의 자식으로 남긴다.

        실전 1 HCX 호출은 LangChain 모델이 아니라서(urllib) on_chat_model_start가 불리지 않는다. 그래서 실전 1이 이미
        재 둔 단계 시간을 끝난 뒤에 옮긴다(T3-30). start_ms·duration_ms가 없는 기록(실전 2 trace 항목 등)은 건너뛴다.
        이름은 실전 1 단계 이름 그대로 둔다.

        T3-31: 모델 단계(gen_ai.request.model이 있음)는 gen_ai.operation.name=chat을 붙여 LLM 호출로 세고 메트릭을 남긴다.
        형식 재시도 단계(analyze.llm_plan_retry 등)도 호출 하나로 센다. HTTP 재시도는 llm.attempts 속성으로만 남는다.
        """
        started = getattr(tool_span, "start_time", None)  # 기록하지 않는 span(NoOp)에는 시작 시각이 없다
        context = trace.set_span_in_context(tool_span)
        for step in steps or []:
            if not isinstance(step, dict) or "start_ms" not in step or "duration_ms" not in step:
                continue
            attributes = {k: v for k, v in (step.get("attributes") or {}).items()
                          if isinstance(v, (str, bool, int, float))}
            error = step.get("status") == "error"
            if (model := attributes.get("gen_ai.request.model")) is not None:
                attributes["gen_ai.operation.name"] = CHAT
                if error:  # 실전 1 llm.status (format_error | timeout | api_error | input_too_long)가 실패 이유
                    attributes["error.type"] = str(attributes.get("llm.status") or "llm_error")
                self._llm_done(str(model), attributes.get("gen_ai.usage.input_tokens"),
                               attributes.get("gen_ai.usage.output_tokens"), float(step["duration_ms"]) / 1000,
                               attributes.get("error.type"))  # 메트릭은 span을 기록하지 않아도 남긴다
            if started is None:
                continue
            begin = started + int(float(step["start_ms"]) * 1e6)
            span = self.tracer.start_span(str(step.get("name", "step")), context=context, start_time=begin,
                                          attributes=attributes)
            if error:
                span.set_status(Status(StatusCode.ERROR))
            span.end(end_time=begin + int(float(step["duration_ms"]) * 1e6))

    def _stage2_span(self, tool_span: Span, steps: list[dict] | None, model: str | None) -> None:
        """실전 2 rerank2 항목(SearchOutput.spans: node·seconds·stage2_*)을 툴 span 아래 `chat <model>` span으로 남긴다.

        rerank2는 CLOVA reranker / HCX를 urllib로 직접 불러 콜백이 오지 않는다(T3-31). 실전 2 trace에는 시작 시각이 없고
        retrieve_* 노드는 병렬이라 앞 노드 seconds를 더하면 틀린다. 그래서 끝에서 거꾸로 잡는다:
        rerank2 끝 = 지금(툴 끝) − rerank2 뒤 노드(gate 등) seconds 합, 시작 = 끝 − stage2_seconds.
        stage2_calls(HTTP 재시도 포함 횟수)는 llm.attempts 속성으로 남기고 LLM 호출은 하나로 센다(실전 1과 같은 규칙).
        docs가 비어 stage 2가 돌지 않았으면(stage2_calls 0 또는 없음) 만들지 않는다.
        """
        entries = [e for e in steps or [] if isinstance(e, dict) and "node" in e]
        at = next((i for i, e in enumerate(entries) if e["node"] == "rerank2"), None)
        if at is None or not entries[at].get("stage2_calls"):
            return
        entry = entries[at]
        model = model or str(entry.get("stage2") or "unknown")
        seconds = float(entry.get("stage2_seconds") or entry.get("seconds") or 0.0)
        tin, tout = entry.get("stage2_input_tokens"), entry.get("stage2_output_tokens")
        error = entry.get("stage2_error")
        error_type = str(error).split(":", 1)[0] if error else None  # "RuntimeError: ..." -> 예외 이름
        self._llm_done(model, tin, tout, seconds, error_type)
        if getattr(tool_span, "start_time", None) is None:
            return
        end = time.time_ns() - int(sum(float(e.get("seconds") or 0.0) for e in entries[at + 1:]) * 1e9)
        attributes = {"gen_ai.operation.name": CHAT, "gen_ai.request.model": model,
                      "gen_ai.usage.input_tokens": tin, "gen_ai.usage.output_tokens": tout,
                      "llm.attempts": entry.get("stage2_calls"), "kosis.stage2": entry.get("stage2"),
                      "error.type": error_type}
        span = self.tracer.start_span(CHAT_PREFIX + model, context=trace.set_span_in_context(tool_span),
                                      start_time=end - int(seconds * 1e9),
                                      attributes={k: v for k, v in attributes.items() if v is not None})
        if error:
            span.set_status(Status(StatusCode.ERROR, str(error)))
        span.end(end_time=end)

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._end(run_id, error)

    # chat model
    def on_chat_model_start(self, serialized, messages, *, run_id: UUID, parent_run_id: UUID | None = None,
                            metadata: dict | None = None, **kwargs: Any) -> None:
        params = kwargs.get("invocation_params") or {}
        model = params.get("model") or params.get("model_name") or (metadata or {}).get("ls_model_name") or "unknown"
        self._open(CHAT_PREFIX + model, run_id, parent_run_id,
                   **{"gen_ai.operation.name": CHAT, "gen_ai.request.model": model})
        self._started[run_id] = (model, time.perf_counter())

    def _chat_model(self, run_id: UUID) -> tuple[str, float] | None:
        """(모델, 걸린 초). on_chat_model_start가 없던 실행(텍스트 LLM 등)이면 None."""
        if (started := self._started.pop(run_id, None)) is None:
            return None
        return started[0], time.perf_counter() - started[1]

    def on_llm_end(self, response, *, run_id: UUID, **kwargs: Any) -> None:
        tin, tout = _tokens(response)
        if (done := self._chat_model(run_id)) is not None:
            self._llm_done(done[0], tin, tout, done[1])
        self._end(run_id, **{"gen_ai.usage.input_tokens": tin, "gen_ai.usage.output_tokens": tout,
                             "gen_ai.response.finish_reasons": _finish_reasons(response)})

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        if (done := self._chat_model(run_id)) is not None:
            self._llm_done(done[0], None, None, done[1], type(error).__name__)
        self._end(run_id, error)


def start_turn(tracer: Tracer, conversation_id: str, index: int,
               meter: Meter | None = None) -> tuple[Span, LiveSpans]:
    """턴의 루트 span(아직 열린 상태)과 콜백. 현재 context에 붙이지 않으므로 호출한 쪽이 직접 root.end()한다.

    서비스(/chat/stream)가 이것을 쓴다(T3-30): sync generator는 Starlette가 next()마다 다른 threadpool 작업에서
    돌리므로, contextvar에 span을 붙였다 떼면 yield 사이에 Context가 달라져 detach가 실패한다. LiveSpans는 부모를
    명시적으로 받으므로 current context가 필요 없다.
    """
    root = tracer.start_span(ROOT_SPAN, attributes={
        "gen_ai.operation.name": "invoke_agent", "gen_ai.conversation.id": conversation_id,
        "kosis.turn.index": index})
    return root, LiveSpans(tracer, root, meter)


@contextmanager
def agent_turn(tracer: Tracer, conversation_id: str, index: int,
               meter: Meter | None = None) -> Iterator[tuple[Span, LiveSpans]]:
    """턴의 루트 span과, 실행 config에 넣을 콜백. 블록 안에서 예외가 나면 루트 span을 error로 표시한다."""
    root, spans = start_turn(tracer, conversation_id, index, meter)
    with trace.use_span(root, end_on_exit=True):
        yield root, spans


def trace_id(span: Span) -> str:
    """span의 trace ID (32자리 hex, Jaeger 검색에 그대로 넣는 값)."""
    return format(span.get_span_context().trace_id, "032x")
