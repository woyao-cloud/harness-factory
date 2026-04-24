"""Streaming — token-by-token output for real-time UX.

Components:

* ``StreamEvent`` — typed events emitted during streaming inference
* ``StreamBuffer`` — collects events into a final ``InferenceResult``
* ``StreamPipeline`` — REPL strategy that streams tokens live
* ``MockStreamProvider`` — deterministic stream for testing
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from .pipeline import Pipeline, PipelineConfig
from .session import Session
from .message_bus import MessageBus, PipelineEvent, PipelinePhase
from .security import SecurityGate
from .types import InferenceConfig, InferenceResult, ToolCall, Usage, UserInput, StepResult

logger = logging.getLogger(__name__)


# ── Stream event types ───────────────────────────────────────────────────────


class StreamType(str, Enum):
    TEXT = "text"
    TOOL_CALL_BEGIN = "tool_call_begin"
    TOOL_CALL_END = "tool_call_end"
    DONE = "done"
    ERROR = "error"


@dataclass(frozen=True)
class StreamEvent:
    """A single event in a streaming inference sequence."""

    type: StreamType
    data: Any = None
    index: int = 0

    @classmethod
    def text(cls, content: str, index: int = 0) -> StreamEvent:
        return cls(StreamType.TEXT, content, index)

    @classmethod
    def tool_call_begin(
        cls, name: str, tool_index: int, input_so_far: dict | None = None
    ) -> StreamEvent:
        return cls(
            StreamType.TOOL_CALL_BEGIN,
            {"name": name, "index": tool_index, "partial_input": input_so_far or {}},
            tool_index,
        )

    @classmethod
    def tool_call_end(
        cls, tool_index: int, name: str, params: dict
    ) -> StreamEvent:
        return cls(
            StreamType.TOOL_CALL_END,
            {"name": name, "index": tool_index, "params": params},
            tool_index,
        )

    @classmethod
    def done(cls, stop_reason: str = "end_turn", usage: Usage | None = None) -> StreamEvent:
        return cls(
            StreamType.DONE,
            {"stop_reason": stop_reason, "usage": usage},
        )

    @classmethod
    def error(cls, message: str) -> StreamEvent:
        return cls(StreamType.ERROR, message)


# ── Stream buffer ────────────────────────────────────────────────────────────


class StreamBuffer:
    """Accumulates stream events into a final ``InferenceResult``.

    Usage::

        buffer = StreamBuffer()
        async for event in llm.stream_complete(messages, tools, config):
            buffer.add(event)
        result = buffer.to_result()
    """

    def __init__(self) -> None:
        self._text_parts: list[str] = []
        self._tool_calls: dict[int, dict] = {}
        self._stop_reason: str = "end_turn"
        self._usage: Usage = Usage()
        self._error: str | None = None

    def add(self, event: StreamEvent) -> None:
        if event.type is StreamType.TEXT:
            self._text_parts.append(str(event.data))
        elif event.type is StreamType.TOOL_CALL_BEGIN:
            idx = event.index
            self._tool_calls[idx] = {"name": event.data["name"], "params": {}}
        elif event.type is StreamType.TOOL_CALL_END:
            idx = event.index
            if idx in self._tool_calls:
                self._tool_calls[idx]["params"] = event.data["params"]
        elif event.type is StreamType.DONE:
            d = event.data or {}
            self._stop_reason = d.get("stop_reason", "end_turn")
            if d.get("usage"):
                self._usage = d["usage"]
        elif event.type is StreamType.ERROR:
            self._error = str(event.data)

    def to_result(self) -> InferenceResult:
        tool_calls = tuple(
            ToolCall(tool_name=tc["name"], params=tc.get("params", {}))
            for tc in sorted(self._tool_calls.values(), key=lambda x: 0)
        )
        return InferenceResult(
            text="".join(self._text_parts),
            tool_calls=tool_calls,
            stop_reason=self._stop_reason,
            usage=self._usage,
        )

    @property
    def partial_text(self) -> str:
        return "".join(self._text_parts)


# ── Streaming provider protocol ──────────────────────────────────────────────


@runtime_checkable
class StreamProvider(Protocol):
    """A provider that can stream — separate from ``LLMProvider``.

    A provider can implement both ``complete`` and ``stream_complete``.
    """

    async def stream_complete(
        self,
        messages: list[dict],
        tools: list[dict],
        config: InferenceConfig,
    ) -> AsyncIterator[StreamEvent]:
        ...


# ── Mock stream provider ─────────────────────────────────────────────────────


class MockStreamProvider(StreamProvider):
    """Deterministic streaming provider for testing.

    Yields configurable sequences of stream events.
    """

    def __init__(self) -> None:
        self._sequences: dict[int, list[StreamEvent]] = {}
        self._call_count = 0

    def add_sequence(self, events: list[StreamEvent]) -> None:
        self._sequences[len(self._sequences)] = events

    async def stream_complete(
        self,
        messages: list[dict],
        tools: list[dict],
        config: InferenceConfig,
    ) -> AsyncIterator[StreamEvent]:
        self._call_count += 1
        seq = self._sequences.get(self._call_count - 1, [
            StreamEvent.text(f"[mock stream] turn {self._call_count}"),
            StreamEvent.done("end_turn", Usage(input_tokens=10, output_tokens=5)),
        ])
        for event in seq:
            yield event
            await asyncio.sleep(0)

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict],
        config: InferenceConfig,
    ) -> InferenceResult:
        """Non-streaming fallback — buffers stream events into a result."""
        buf = StreamBuffer()
        async for event in self.stream_complete(messages, tools, config):
            buf.add(event)
        return buf.to_result()


# ── StreamPipeline ───────────────────────────────────────────────────────────


class StreamPipeline(Pipeline):
    """REPL strategy that streams tokens as they arrive.

    Each token chunk is emitted on the ``MessageBus`` as a ``StreamEvent``-wrapped
    payload.  Use ``StreamBuffer`` on the consumer side to aggregate events.

    Falls back to non-streaming if the LLM provider doesn't support streaming.
    """

    def __init__(
        self,
        config: PipelineConfig,
        session: Session,
        llm: LLMProvider,
        bus: MessageBus,
        security: SecurityGate,
    ) -> None:
        super().__init__(config, session, llm, bus, security)
        self._stream_llm = (
            llm if isinstance(llm, StreamProvider) else None
        )

    async def turn(self, user_input: UserInput) -> StepResult:
        self._session.record_user_input(user_input)

        self._bus.emit(
            PipelineEvent.BEFORE_INFERENCE,
            phase=PipelinePhase.BEFORE_INFERENCE,
        )

        supplemental = (
            self._session.context_manager.safe_point_before_inference()
        )

        prompt = self._build_llm_messages()
        tools = self._llm_tool_format()

        if self._stream_llm:
            result = await self._stream_turn(prompt, tools)
        else:
            result = await self._llm.complete(
                prompt, tools, self._cfg.inference_config,
            )
            self._session.record_assistant_text(result.text)

        final_text, rounds = await self._tool_loop(result)

        self._bus.emit(
            PipelineEvent.PIPELINE_TURN,
            phase=PipelinePhase.IDLE,
            turn=self._session.turn_count,
            tool_rounds=rounds,
        )

        return StepResult(
            text=final_text,
            tool_calls=result.tool_calls,
            turn_complete=True,
            supplemental_context=supplemental,
        )

    async def _stream_turn(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> InferenceResult:
        buf = StreamBuffer()
        async for event in self._stream_llm.stream_complete(
            messages, tools, self._cfg.inference_config,
        ):
            buf.add(event)
            self._bus.emit(
                PipelineEvent.OUTPUT_TEXT
                if event.type is StreamType.TEXT
                else PipelineEvent.PIPELINE_STEP,
                phase=PipelinePhase.INFERENCE,
                stream_event=event,
            )

        result = buf.to_result()
        self._session.record_assistant_text(result.text)
        return result

    async def run(self, user_input: UserInput) -> StepResult:
        return await self.turn(user_input)
