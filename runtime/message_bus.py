"""Event-driven message bus — decouples pipeline components via pub/sub.

Supports:
* Synchronous event emission
* Pre/Post hook pairs (``before_X`` / ``after_X``)
* Multiple subscribers per event
* Structured ``EventPayload`` with phase info
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

from .types import EventPayload, PipelineEvent, PipelinePhase

logger = logging.getLogger(__name__)

EventHandler = Callable[[EventPayload], None]
"""Signature: ``def handler(payload: EventPayload) -> None``"""


class MessageBus:
    """Lightweight in-process event bus.

    Usage::

        bus = MessageBus()
        bus.on(PipelineEvent.BEFORE_INFERENCE, my_handler)
        bus.emit(PipelineEvent.BEFORE_INFERENCE, phase=PipelinePhase.INFERENCE)
    """

    def __init__(self) -> None:
        self._subscribers: dict[PipelineEvent, list[EventHandler]] = defaultdict(list)
        self._history: list[EventPayload] = []

    # ── Subscription ─────────────────────────────────────────────────────

    def on(self, event: PipelineEvent, handler: EventHandler) -> None:
        """Subscribe to an event."""
        self._subscribers[event].append(handler)

    def off(self, event: PipelineEvent, handler: EventHandler) -> None:
        """Unsubscribe a handler."""
        self._subscribers[event] = [
            h for h in self._subscribers[event] if h is not handler
        ]

    # ── Emission ─────────────────────────────────────────────────────────

    def emit(
        self,
        event: PipelineEvent,
        phase: PipelinePhase = PipelinePhase.IDLE,
        **data: Any,
    ) -> None:
        """Emit an event with structured payload."""
        payload = EventPayload(event=event, phase=phase, data=data)
        self._history.append(payload)
        for handler in self._subscribers.get(event, []):
            try:
                handler(payload)
            except Exception:
                logger.exception(
                    "Handler %s failed for event %s", handler.__name__, event.value
                )

    # ── History ──────────────────────────────────────────────────────────

    @property
    def history(self) -> tuple[EventPayload, ...]:
        return tuple(self._history)

    def clear_history(self) -> None:
        self._history.clear()

    def last_event(self, event_type: PipelineEvent | None = None) -> EventPayload | None:
        """Get the most recent event, optionally filtered by type."""
        if event_type is None:
            return self._history[-1] if self._history else None
        for p in reversed(self._history):
            if p.event == event_type:
                return p
        return None
