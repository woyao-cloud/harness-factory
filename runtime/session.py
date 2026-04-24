"""Session management — conversation lifecycle with ContextManager integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from context_manager import ContextManager, ManagerConfig, Message, MessageType
from .message_bus import EventHandler, MessageBus
from .types import EventPayload, PipelineEvent, PipelinePhase, SessionState, UserInput

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionConfig:
    session_id: str = ""
    total_budget: int = 64000
    context_manager: ContextManager | None = None
    store_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class Session:
    """A single conversation session.

    Wraps ``ContextManager`` and emits lifecycle events via ``MessageBus``.

    If a ``ContextManager`` is provided via ``config.context_manager``,
    it is used directly.  Otherwise a new one is created with the given
    ``total_budget`` and ``session_id``.
    """

    def __init__(
        self,
        config: SessionConfig,
        bus: MessageBus,
    ) -> None:
        self._cfg = config
        self._bus = bus
        self._id = config.session_id or f"sess_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        self._state = SessionState.CREATED

        if config.context_manager is not None:
            self._cm = config.context_manager
        else:
            from context_manager import StoreConfig
            self._cm = ContextManager(
                ManagerConfig(
                    total_budget=config.total_budget,
                    session_id=self._id,
                )
            )
        self._started_at: datetime | None = None
        self._ended_at: datetime | None = None
        self._turn_count = 0

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def id(self) -> str:
        return self._id

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def context_manager(self) -> ContextManager:
        return self._cm

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def bus(self) -> MessageBus:
        return self._bus

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Transition to ACTIVE and emit event."""
        if self._state is not SessionState.CREATED:
            raise RuntimeError(f"Cannot start session in state {self._state.name}")
        self._state = SessionState.ACTIVE
        self._started_at = datetime.now(timezone.utc)
        self._bus.emit(
            PipelineEvent.SESSION_STARTED,
            phase=PipelinePhase.IDLE,
            session_id=self._id,
        )
        logger.info("Session %s started", self._id)

    def pause(self) -> None:
        if self._state is not SessionState.ACTIVE:
            raise RuntimeError(f"Cannot pause session in state {self._state.name}")
        self._state = SessionState.PAUSED
        logger.info("Session %s paused", self._id)

    def resume(self) -> None:
        if self._state is not SessionState.PAUSED:
            raise RuntimeError(f"Cannot resume session in state {self._state.name}")
        self._state = SessionState.ACTIVE
        self._bus.emit(
            PipelineEvent.SESSION_STARTED,
            phase=PipelinePhase.IDLE,
            session_id=self._id,
        )
        logger.info("Session %s resumed", self._id)

    def close(self) -> None:
        """Close session — persist compressed data and emit event."""
        if self._state is SessionState.CLOSED:
            return
        self._state = SessionState.CLOSED
        self._ended_at = datetime.now(timezone.utc)
        self._cm.close()
        self._bus.emit(
            PipelineEvent.SESSION_CLOSED,
            phase=PipelinePhase.CLOSED,
            session_id=self._id,
            turn_count=self._turn_count,
        )
        logger.info("Session %s closed after %d turns", self._id, self._turn_count)

    # ── Message recording ────────────────────────────────────────────────

    def record_user_input(self, user_input: UserInput) -> None:
        """Record user input as a message and emit event."""
        msg = Message(
            role="user",
            content=user_input.text,
            msg_type=MessageType.USER_INPUT,
        )
        self._cm.add_message(msg)
        self._turn_count += 1
        self._bus.emit(
            PipelineEvent.USER_INPUT,
            phase=PipelinePhase.PARSING_INPUT,
            text=user_input.text,
        )

    def record_assistant_text(self, text: str) -> None:
        """Record assistant text response."""
        msg = Message(
            role="assistant",
            content=text,
            msg_type=MessageType.MODEL_REPLY,
        )
        self._cm.add_message(msg)

    def record_tool_result(self, tool_name: str, result: Any, error: str | None = None) -> None:
        """Record tool execution result."""
        content = str(result) if error is None else f"Error: {error}"
        msg = Message(
            role="tool_result",
            content=content[:10000],  # cap oversized results
            msg_type=MessageType.TOOL_RESULT,
        )
        self._cm.add_message(msg)

    # ── Cross-session ────────────────────────────────────────────────────

    def lookup_anchors(self, source_path: str):
        """Cross-session anchor lookup via ContextManager."""
        return self._cm.lookup_anchor(source_path)
