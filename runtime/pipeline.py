"""Pipeline — REPL loop strategies for the HarnessRuntime.

Two strategies:
* ``InteractivePipeline`` — wait for user input, respond, repeat.
* ``AutoPipeline`` — run autonomously until completion or tool limit.

Both share the same inner tool-loop mechanism via the base class.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from context_manager import ContextManager

from .llm_provider import LLMProvider
from .message_bus import MessageBus
from .security import SecurityGate
from .session import Session
from .types import (
    EventPayload,
    InferenceConfig,
    InferenceResult,
    PipelineEvent,
    PipelinePhase,
    StepResult,
    ToolCall,
    ToolDefinition,
    UserInput,
)

logger = logging.getLogger(__name__)

ToolExecutorFn = Callable[..., Any]


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PipelineConfig:
    max_tool_rounds: int = 25
    inference_config: InferenceConfig = field(default_factory=InferenceConfig)
    tool_as_messages: bool = True


# ── Base pipeline ────────────────────────────────────────────────────────────


class Pipeline(ABC):
    """Common tool-loop machinery shared by all pipeline strategies."""

    def __init__(
        self,
        config: PipelineConfig,
        session: Session,
        llm: LLMProvider,
        bus: MessageBus,
        security: SecurityGate,
    ) -> None:
        self._cfg = config
        self._session = session
        self._llm = llm
        self._bus = bus
        self._security = security

        # Tool registry
        self._tool_defs: dict[str, ToolDefinition] = {}
        self._tool_executors: dict[str, ToolExecutorFn] = {}

        # Tool call context — reused across the inner tool loop
        self._tool_call_id: str = ""

    # ── Tool registration ───────────────────────────────────────────────

    def register_tool(
        self,
        definition: ToolDefinition,
        executor: ToolExecutorFn,
    ) -> None:
        self._tool_defs[definition.name] = definition
        self._tool_executors[definition.name] = executor

    def register_tools(
        self,
        definitions: list[ToolDefinition],
        executors: list[ToolExecutorFn],
    ) -> None:
        for d, e in zip(definitions, executors):
            self.register_tool(d, e)

    @property
    def tool_definitions(self) -> list[ToolDefinition]:
        return list(self._tool_defs.values())

    # ── Tool call loop ──────────────────────────────────────────────────

    async def _tool_loop(
        self,
        result: InferenceResult,
    ) -> tuple[str, int]:
        """Inner tool-call loop.

        Returns (final_text, tool_rounds_used).
        """
        final_text = result.text
        rounds = 0

        while result.tool_calls and rounds < self._cfg.max_tool_rounds:
            rounds += 1

            for tool_call in result.tool_calls:
                # Security gate
                approval = self._security.approve(tool_call)
                if approval.decision.value == "DENY":
                    self._bus.emit(
                        PipelineEvent.TOOL_DENIED,
                        phase=PipelinePhase.BEFORE_TOOL,
                        tool_name=tool_call.tool_name,
                        reason=approval.reason,
                    )
                    self._session.record_tool_result(
                        tool_call.tool_name,
                        f"Permission denied: {approval.reason}",
                        error="denied",
                    )
                    continue

                self._bus.emit(
                    PipelineEvent.BEFORE_TOOL,
                    phase=PipelinePhase.BEFORE_TOOL,
                    tool_call=tool_call,
                )

                # ContextManager: dependency detection before execution
                self._session.context_manager.record_tool_call(
                    tool_call.tool_name,
                    dict(tool_call.params),
                )

                # Execute tool
                try:
                    executor = self._tool_executors.get(tool_call.tool_name)
                    if executor is None:
                        raise ValueError(f"No executor for tool '{tool_call.tool_name}'")

                    tool_result = executor(**tool_call.params)
                    # Handle both sync and async executors
                    if hasattr(tool_result, "__await__"):
                        tool_result = await tool_result

                    self._session.record_tool_result(tool_call.tool_name, tool_result)

                except Exception as e:
                    logger.exception("Tool %s failed", tool_call.tool_name)
                    self._session.record_tool_result(
                        tool_call.tool_name, str(e), error=str(e)
                    )

                self._bus.emit(
                    PipelineEvent.AFTER_TOOL,
                    phase=PipelinePhase.AFTER_TOOL,
                    tool_name=tool_call.tool_name,
                )

            # Continue inference with tool results
            self._bus.emit(
                PipelineEvent.BEFORE_INFERENCE,
                phase=PipelinePhase.BEFORE_INFERENCE,
                tool_round=rounds,
            )

            prompt = self._build_llm_messages()
            tools = self._llm_tool_format()

            result = await self._llm.complete(
                prompt,
                tools,
                self._cfg.inference_config,
            )

            self._session.record_usage(result.usage)
            self._session.record_assistant_text(result.text)
            final_text = result.text

            self._bus.emit(
                PipelineEvent.AFTER_INFERENCE,
                phase=PipelinePhase.AFTER_INFERENCE,
                tool_round=rounds,
                usage=result.usage,
            )

        return final_text, rounds

    def _build_llm_messages(self) -> list[dict]:
        """Build messages for the LLM from ContextManager's active messages."""
        return [
            {"role": m.role, "content": m.content}
            for m in self._session.context_manager.active_messages
        ]

    def _llm_tool_format(self) -> list[dict]:
        """Convert tool definitions to provider-native format."""
        # Default to Anthropic format — provider can transform as needed
        return [t.to_anthropic_tool() for t in self.tool_definitions]

    # ── Abstract ────────────────────────────────────────────────────────

    @abstractmethod
    async def turn(self, user_input: UserInput) -> StepResult:
        """Process one user input through the REPL loop."""
        ...

    @abstractmethod
    async def run(self, user_input: UserInput) -> StepResult:
        """Full run — from input to final output."""
        ...


# ── Interactive ──────────────────────────────────────────────────────────────


class InteractivePipeline(Pipeline):
    """Standard interactive REPL: input → inference → tool loop → output.

    Safe points are triggered before each inference call, allowing the
    ContextManager to compress history and inject supplemental context.
    """

    async def turn(self, user_input: UserInput) -> StepResult:
        self._session.record_user_input(user_input)

        self._bus.emit(
            PipelineEvent.BEFORE_INFERENCE,
            phase=PipelinePhase.BEFORE_INFERENCE,
        )

        # Safe point: collect async plans, inject supplemental context
        supplemental = (
            self._session.context_manager.safe_point_before_inference()
        )

        prompt = self._build_llm_messages()
        tools = self._llm_tool_format()

        result = await self._llm.complete(
            prompt,
            tools,
            self._cfg.inference_config,
        )

        self._session.record_usage(result.usage)
        self._session.record_assistant_text(result.text)
        self._bus.emit(
            PipelineEvent.AFTER_INFERENCE,
            phase=PipelinePhase.AFTER_INFERENCE,
            usage=result.usage,
        )

        # Inner tool loop
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

    async def run(self, user_input: UserInput) -> StepResult:
        return await self.turn(user_input)


# ── Auto ─────────────────────────────────────────────────────────────────────


class AutoPipeline(Pipeline):
    """Autonomous mode: runs the tool loop until completion or limit.

    Starts with a user-provided goal text, then runs inference → tools
    repeatedly until the model produces a text-only response (no tool calls).
    """

    MAX_AUTO_TURNS = 50

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

        result = await self._llm.complete(
            prompt,
            tools,
            self._cfg.inference_config,
        )

        self._session.record_usage(result.usage)
        self._session.record_assistant_text(result.text)
        self._bus.emit(
            PipelineEvent.AFTER_INFERENCE,
            phase=PipelinePhase.AFTER_INFERENCE,
        )

        # Auto: loop until no more tool calls
        text, rounds = await self._tool_loop(result)
        session_complete = rounds < self._cfg.max_tool_rounds

        self._bus.emit(
            PipelineEvent.PIPELINE_TURN,
            phase=PipelinePhase.IDLE,
            tool_rounds=rounds,
        )

        return StepResult(
            text=text,
            turn_complete=True,
            session_complete=session_complete,
            supplemental_context=supplemental,
        )

    async def run(self, user_input: UserInput) -> StepResult:
        return await self.turn(user_input)
