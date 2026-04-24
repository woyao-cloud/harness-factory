"""ContextManager — orchestrator of the full context lifecycle.

This is the primary entry point for the HarnessRuntime.  It ties together:

* **L1 (active messages)** — the current working set in the inference loop
* **LayeredStore (L2 + L3)** — persistent storage for compressed records
* **AsyncCompressor** — scoring, strategy chain, and async plan creation
* **DependencyDetector** — sniffing tool-call references to compressed content
* **Restorer** — resolving dependencies via ``supplemental_context`` injection

Usage::

    cm = ContextManager(
        ManagerConfig(total_budget=64000, session_id="sess_001"),
        store=LayeredStore(StoreConfig(db_path="my_project.db")),
    )

    cm.add_message(user_msg)

    # Before LLM inference:
    prompt = cm.build_prompt()  # may inject supplemental_context

    # After tool execution:
    cm.record_tool_call(tool_name, params)
    cm.add_message(tool_result_msg)

    # On session end:
    cm.close()
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import auto, Enum
from typing import Any

from .compressor import ApplyResult, AsyncCompressor, CompressorConfig, StrategyChain
from .dependency import DependencyDetector, DetectorConfig
from .restorer import Restorer, RestorerConfig
from .store import LayeredStore
from .types import (
    AnchorRef,
    BudgetState,
    CompressedRecord,
    CompressionPlan,
    CompressTrigger,
    Message,
    MessageType,
    RestoredSnippet,
    ToolCall,
)

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ManagerConfig:
    total_budget: int = 64000  # Max tokens for L1
    session_id: str = ""
    warn_ratio: float = 0.65
    compress_ratio: float = 0.80
    critical_ratio: float = 0.92
    hard_limit_ratio: float = 0.98
    async_pre_compress: bool = True
    auto_supplemental_context: bool = True


@dataclass(frozen=True)
class SafePoint:
    """A point in the REPL cycle where compression can safely be applied."""

    phase: str  # before_inference | before_user_input | after_tool


# ── ContextManager ──────────────────────────────────────────────────────────


class ContextManager:
    """Orchestrate context window management for a HarnessRuntime.

    Thread-safe: the main REPL loop and the async compressor communicate
    through thread-safe channels.
    """

    def __init__(
        self,
        config: ManagerConfig,
        store: LayeredStore | None = None,
        compressor: AsyncCompressor | None = None,
        detector: DependencyDetector | None = None,
        restorer: Restorer | None = None,
    ) -> None:
        self._cfg = config
        self._session_id = config.session_id or uuid.uuid4().hex[:12]

        # L1 — active messages in the inference window
        self._messages: list[Message] = []

        # Storage layer
        from .store import StoreConfig as _SC

        self._store = store or LayeredStore(_SC())

        # Compression engine
        compressor_cfg = CompressorConfig()
        self._compressor = compressor or AsyncCompressor(compressor_cfg)

        # Detection & restoration
        self._detector = detector or DependencyDetector(DetectorConfig())
        self._restorer = restorer or Restorer(RestorerConfig())

        # Pending supplemental context (set by _check_and_restore)
        self._pending_supplemental: list[RestoredSnippet] = []

        # Active anchors (anchors for currently compressed messages in L1)
        self._active_anchors: dict[str, AnchorRef] = {}

        # Async compression state
        self._async_plan: CompressionPlan | None = None
        self._lock = threading.Lock()

        # Session start time
        self._started_at = datetime.now(timezone.utc)
        self._closed = False

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def active_messages(self) -> tuple[Message, ...]:
        """Read-only snapshot of L1."""
        return tuple(self._messages)

    @property
    def budget(self) -> BudgetState:
        """Current budget snapshot."""
        usage = sum(m.token_estimate for m in self._messages)
        ratio = usage / self._cfg.total_budget if self._cfg.total_budget > 0 else 0
        trigger = (
            CompressTrigger.HARD_LIMIT
            if ratio >= self._cfg.hard_limit_ratio
            else CompressTrigger.BUDGET_CRITICAL
            if ratio >= self._cfg.critical_ratio
            else CompressTrigger.BUDGET_COMPRESS
            if ratio >= self._cfg.compress_ratio
            else CompressTrigger.BUDGET_WARN
            if ratio >= self._cfg.warn_ratio
            else None
        )
        return BudgetState(
            current_usage=usage,
            total_budget=self._cfg.total_budget,
            ratio=round(ratio, 4),
            trigger=trigger,
        )

    @property
    def store_path(self) -> str:
        """Path to the L2 SQLite store used by this ContextManager."""
        return self._store.store_path

    # ── Message lifecycle ────────────────────────────────────────────────

    def add_message(self, msg: Message) -> Message:
        """Add a message to L1 and check budget.

        Returns the message (with updated metadata including session_id).
        """
        if self._closed:
            raise RuntimeError("ContextManager is closed")

        enriched = Message(
            id=msg.id,
            role=msg.role,
            content=msg.content,
            msg_type=msg.msg_type,
            version=msg.version,
            metadata={
                **msg.metadata,
                "session_id": self._session_id,
            },
            timestamp=msg.timestamp,
        )

        self._messages.append(enriched)
        self._check_budget()

        return enriched

    def _check_budget(self) -> None:
        """Evaluate budget and trigger compression if needed."""
        state = self.budget

        # Hard limit — emergency compress, no async
        if state.trigger is CompressTrigger.HARD_LIMIT:
            logger.warning("Budget at %.1f%% — emergency compression", state.ratio * 100)
            self._compress(CompressTrigger.HARD_LIMIT)

        # Critical — force compression now
        elif state.trigger is CompressTrigger.BUDGET_CRITICAL:
            logger.info("Budget at %.1f%% — critical compression", state.ratio * 100)
            self._compress(CompressTrigger.BUDGET_CRITICAL)

        # Compress threshold — compress now
        elif state.trigger is CompressTrigger.BUDGET_COMPRESS:
            logger.info("Budget at %.1f%% — compressing", state.ratio * 100)
            self._compress(CompressTrigger.BUDGET_COMPRESS)

        # Warn threshold — async pre-compression
        elif state.trigger is CompressTrigger.BUDGET_WARN:
            if self._cfg.async_pre_compress:
                logger.debug("Budget at %.1f%% — submitting async plan", state.ratio * 100)
                self._submit_async_plan()

    # ── Compression ──────────────────────────────────────────────────────

    def _compress(self, trigger: CompressTrigger) -> None:
        """Synchronously compress messages.

        Called when budget exceeds compress / critical / hard_limit thresholds.
        """
        plan = self._compressor.create_plan(
            self._messages,
            trigger=trigger,
            target_savings=self._estimate_target_savings(trigger),
        )
        if not plan.candidates:
            return

        self._apply_and_persist(plan)

    def _estimate_target_savings(self, trigger: CompressTrigger) -> int:
        """How many tokens we need to free up."""
        state = self.budget
        if trigger is CompressTrigger.HARD_LIMIT:
            target_ratio = self._cfg.compress_ratio  # Get back to 80%
        elif trigger is CompressTrigger.BUDGET_CRITICAL:
            target_ratio = self._cfg.compress_ratio
        elif trigger is CompressTrigger.BUDGET_COMPRESS:
            target_ratio = self._cfg.warn_ratio
        else:
            target_ratio = self._cfg.warn_ratio

        current = state.current_usage
        target = int(self._cfg.total_budget * target_ratio)
        return max(current - target, 0)

    def _apply_and_persist(self, plan: CompressionPlan) -> None:
        """Apply compression plan and persist to store."""
        result: ApplyResult = self._compressor.apply_plan(
            plan, self._messages, self._session_id
        )
        if not result.savings:
            return

        # Atomically swap L1
        self._messages = list(result.messages)

        # Persist to L2
        for record in result.records:
            self._store.write(record)
        for anchor in result.anchors:
            self._store.write_anchor(anchor)
            self._active_anchors[anchor.anchor_id] = anchor

        logger.info(
            "Compressed %d items, saved %d tokens (new L1 size: %d msgs)",
            len(plan.candidates),
            result.savings,
            result.message_count,
        )

    # ── Async plan submission ────────────────────────────────────────────

    def _submit_async_plan(self) -> None:
        """Submit a background compression plan.

        The plan is collected at the next safe point.
        """
        self._compressor.submit_plan(self._messages, CompressTrigger.BUDGET_WARN)

    def _collect_async_plan(self) -> CompressionPlan | None:
        """Check if an async plan is ready."""
        return self._compressor.collect_plan()

    # ── Safe points ──────────────────────────────────────────────────────

    def safe_point_before_inference(self) -> str | None:
        """Called before LLM inference.

        Returns supplemental context if restoration is needed, else None.
        This is a safe point for:
        1. Collecting pending async compression plans
        2. Checking dependencies and restoring content
        """
        # Collect async plan if available
        plan = self._collect_async_plan()
        if plan and plan.candidates:
            self._apply_and_persist(plan)

        # Return pending supplemental context
        if self._pending_supplemental and self._cfg.auto_supplemental_context:
            return self._restorer.format_supplemental_context(self._pending_supplemental)
        return None

    def safe_point_after_tool(self, tool_call: ToolCall) -> None:
        """Called after a tool call is parsed from LLM output.

        Runs dependency detection → restoration → queues supplemental context.
        """
        if not self._active_anchors:
            return

        hits = self._detector.detect(tool_call, list(self._active_anchors.values()))
        if not hits:
            return

        snippets = self._restorer.restore(hits, self._store)
        if not snippets:
            # Try degradation fallback
            snippets = [
                s
                for hit in hits
                if (s := self._restorer.degrade(hit, self._store)) is not None
            ]

        if snippets:
            self._pending_supplemental = snippets
            logger.debug(
                "Queued %d restored snippets for next inference", len(snippets)
            )

    # ── Prompt building ─────────────────────────────────────────────────

    def build_prompt(self) -> list[Message]:
        """Build the full prompt for LLM inference.

        Includes messages plus any pending supplemental context injected
        as a temporary block.
        """
        supplemental = self.safe_point_before_inference()
        if supplemental:
            # Inject supplemental context as a temporary message just before
            # the latest user message
            supp_msg = Message(
                role="system",
                content=supplemental,
                msg_type=MessageType.SUPPLEMENTAL,
            )
            result = list(self._messages[:-1]) + [supp_msg] + [self._messages[-1]]
            self._pending_supplemental = []
            return result

        return list(self._messages)

    def record_tool_call(self, tool_name: str, params: dict[str, Any]) -> None:
        """Record and check a tool call for compressed-content dependencies."""
        tc = ToolCall(tool_name=tool_name, params=params, raw_params=str(params))
        self.safe_point_after_tool(tc)

    # ── Session lifecycle ────────────────────────────────────────────────

    def close(self) -> None:
        """Finalize the session and release resources."""
        if self._closed:
            return
        self._closed = True
        self._store.close()
        logger.info("Session %s closed", self._session_id)

    def reset(self) -> None:
        """Reset L1 entirely (keep L2/L3 data for cross-session use)."""
        self._messages = []
        self._active_anchors = {}
        self._pending_supplemental = []
        self._async_plan = None

    # ── Cross-session support ────────────────────────────────────────────

    def lookup_anchor(self, source_path: str) -> list[AnchorRef]:
        """Look up compressed anchors by source path (cross-session)."""
        return self._store.cross_session_lookup(source_path)

    def export_session_history(self) -> tuple[list[CompressedRecord], list[AnchorRef]]:
        """Export all compressed data for this session."""
        records = list(self._store.list_session_records(self._session_id))
        anchors = self._store.list_session_anchors(self._session_id)
        return records, anchors
