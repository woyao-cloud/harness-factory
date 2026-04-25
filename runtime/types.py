"""Runtime-level types — session, pipeline, harness, and event definitions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import auto, Enum
from typing import Any, Callable, Protocol


# ── Enums ────────────────────────────────────────────────────────────────────


class SessionState(Enum):
    CREATED = auto()
    ACTIVE = auto()
    PAUSED = auto()
    CLOSED = auto()


class PipelinePhase(Enum):
    """Current phase of the REPL loop."""

    IDLE = auto()
    PARSING_INPUT = auto()
    BEFORE_INFERENCE = auto()
    INFERENCE = auto()
    AFTER_INFERENCE = auto()
    PARSING_RESPONSE = auto()
    BEFORE_TOOL = auto()
    TOOL_EXECUTION = auto()
    AFTER_TOOL = auto()
    OUTPUT = auto()
    ERROR = auto()
    CLOSED = auto()


class PipelineEvent(str, Enum):
    """Events emitted by the pipeline on the message bus."""

    SESSION_STARTED = "session.started"
    SESSION_CLOSED = "session.closed"
    USER_INPUT = "user.input"
    BEFORE_INFERENCE = "before.inference"
    AFTER_INFERENCE = "after.inference"
    TOOL_CALL_DETECTED = "tool.call_detected"
    BEFORE_TOOL = "before.tool"
    AFTER_TOOL = "after.tool"
    TOOL_DENIED = "tool.denied"
    OUTPUT_TEXT = "output.text"
    COMPRESSION_TRIGGERED = "compression.triggered"
    SUPPLEMENTAL_INJECTED = "supplemental.injected"
    ERROR = "error"
    PIPELINE_STEP = "pipeline.step"
    PIPELINE_TURN = "pipeline.turn"


# ── Tool definitions ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolParam:
    """Schema for a single tool parameter."""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = False


@dataclass(frozen=True)
class ToolDefinition:
    """A tool that a harness exposes to the LLM."""

    name: str
    description: str = ""
    parameters: tuple[ToolParam, ...] = ()

    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        p.name: {
                            "type": p.type,
                            "description": p.description,
                        }
                        for p in self.parameters
                    },
                    "required": [p.name for p in self.parameters if p.required],
                },
            },
        }

    def to_anthropic_tool(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    p.name: {
                        "type": p.type,
                        "description": p.description,
                    }
                    for p in self.parameters
                },
                "required": [p.name for p in self.parameters if p.required],
            },
        }


ToolExecutor = Callable[..., Any]
"""Signature: ``async def fn(**params) -> Any``"""


# ── Inference types ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InferenceConfig:
    """Parameters for a single LLM inference call."""

    model: str = "deepseek-v4-flash:cloud"
    max_tokens: int = 4096
    temperature: float = 0.7
    stop_sequences: tuple[str, ...] = ()
    system_prompt: str = ""
    thinking: bool = False
    thinking_budget: int | None = None


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int | None = None
    cache_read_tokens: int | None = None


@dataclass(frozen=True)
class InferenceResult:
    """Result from a single LLM inference call."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)
    model: str = ""


# ── Tool calls (runtime-level wrapper) ───────────────────────────────────────


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation parsed from LLM output.

    This is a runtime-level type (distinct from context_manager.types.ToolCall)
    that carries an ID and result status alongside the call params.
    """

    id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:8]}")
    tool_name: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | approved | denied | completed | failed
    result: Any = None
    error: str | None = None


# ── User input ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UserInput:
    """Wrapped user input with metadata."""

    text: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    attachments: tuple[str, ...] = ()


# ── Harness specification ────────────────────────────────────────────────────


@dataclass(frozen=True)
class HarnessSpec:
    """Blueprint for a Harness — defines what tools, agents, and config it uses."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    tools: tuple[ToolDefinition, ...] = ()
    system_prompt_template: str = ""
    pipeline_strategy: str = "interactive"  # interactive | auto | stream
    default_model: str = "deepseek-v4-flash:cloud"
    config: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def resolve_tool(self, name: str) -> ToolDefinition | None:
        return next((t for t in self.tools if t.name == name), None)


# ── Event payloads ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EventPayload:
    """Payload for a message bus event."""

    event: PipelineEvent
    phase: PipelinePhase = PipelinePhase.IDLE
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Pipeline step result ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class StepResult:
    """Result of a single pipeline step — either a turn result or a signal."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    turn_complete: bool = False
    session_complete: bool = False
    error: str | None = None
    supplemental_context: str | None = None
