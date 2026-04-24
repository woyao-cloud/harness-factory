"""Compression engine — scoring, strategy chain, and async operation."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .types import (
    AnchorRef,
    CompressedRecord,
    CompressionCandidate,
    CompressionLevel,
    CompressionPlan,
    CompressTrigger,
    Message,
    MessageType,
)


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CompressorConfig:
    """Tuning knobs for the compression engine."""

    max_truncate_lines: int = 30
    max_truncate_chars: int = 3000
    summarization_model: str = "current"  # Use the same LLM
    min_message_tokens_for_compress: int = 50
    max_candidates_per_round: int = 5
    score_weights: dict[str, float] = field(
        default_factory=lambda: {
            "age": 0.25,
            "size": 0.30,
            "type_bonus": 0.25,
            "tool_result_size": 0.20,
        }
    )


# ── Strategy definition ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class StrategyResult:
    """Result of applying a compression strategy to one message."""

    message_id: str
    replacement: Message | None  # None means drop entirely
    record: CompressedRecord | None
    anchor: AnchorRef | None
    savings: int  # tokens saved


CompressStrategy = Callable[
    [CompressionCandidate, Message, CompressorConfig],
    StrategyResult,
]


# ── Strategy implementations ─────────────────────────────────────────────────


def _truncate_strategy(
    candidate: CompressionCandidate,
    msg: Message,
    cfg: CompressorConfig,
) -> StrategyResult:
    """Level 1: Truncate long content to first N lines/chars."""
    lines = candidate.content.splitlines()
    if len(lines) > cfg.max_truncate_lines:
        truncated = "\n".join(lines[: cfg.max_truncate_lines])
        omitted = len(lines) - cfg.max_truncate_lines
        new_content = f"{truncated}\n... ({omitted} lines omitted)"
    elif len(candidate.content) > cfg.max_truncate_chars:
        new_content = candidate.content[: cfg.max_truncate_chars] + "..."
    else:
        new_content = candidate.content  # no truncation needed

    savings = candidate.token_count - (len(new_content) // 4)
    anchor_id = f"anc_{uuid.uuid4().hex[:10]}"

    record = CompressedRecord(
        anchor_id=anchor_id,
        session_id=msg.metadata.get("session_id", ""),
        original_content=candidate.content,
        summary=f"Truncated from {candidate.token_count}t",
        message_ids=(msg.id,),
        metadata={"source_type": candidate.message_type.name, "level": "truncate"},
    )

    anchor = AnchorRef(
        anchor_id=anchor_id,
        session_id=msg.metadata.get("session_id", ""),
        source_message_ids=(msg.id,),
        summary=f"Truncated ({omitted} lines omitted)"
        if len(lines) > cfg.max_truncate_lines
        else "Truncated",
        compressed_level=CompressionLevel.LEVEL_1_TRUNCATE,
        metadata={"key_terms": lines[0].split()[:5] if lines else []},
    )

    replacement = Message(
        id=msg.id,
        role=msg.role,
        content=f"[anchor: {anchor_id}] {new_content}",
        msg_type=MessageType.COMPRESSED_BLOCK,
        version=msg.version + 1,
        metadata={
            **msg.metadata,
            "anchor_id": anchor_id,
            "compressed": True,
            "level": "truncate",
        },
    )

    return StrategyResult(
        message_id=msg.id,
        replacement=replacement,
        record=record,
        anchor=anchor,
        savings=max(savings, 0),
    )


def _summarize_strategy(
    candidate: CompressionCandidate,
    msg: Message,
    cfg: CompressorConfig,
) -> StrategyResult:
    """Level 2: Replace content with a summary placeholder.

    Full summarization uses the LLM; this path generates a structural summary
    (type + length + line-count) as a placeholder for the real LLM summary.
    """
    lines = candidate.content.splitlines()
    line_count = len(lines)
    type_label = candidate.message_type.name
    summary = (
        f"[Compressed: {type_label}, {candidate.token_count}t, {line_count} lines]"
    )

    savings = candidate.token_count - (len(summary) // 4)
    anchor_id = f"anc_{uuid.uuid4().hex[:10]}"

    record = CompressedRecord(
        anchor_id=anchor_id,
        session_id=msg.metadata.get("session_id", ""),
        original_content=candidate.content,
        summary=summary,
        message_ids=(msg.id,),
        metadata={
            "source_type": candidate.message_type.name,
            "level": "summarize",
            "line_count": line_count,
        },
    )

    anchor = AnchorRef(
        anchor_id=anchor_id,
        session_id=msg.metadata.get("session_id", ""),
        source_message_ids=(msg.id,),
        summary=summary,
        compressed_level=CompressionLevel.LEVEL_2_SUMMARIZE,
        metadata={"key_terms": lines[0].split()[:5] if lines else []},
    )

    replacement = Message(
        id=msg.id,
        role=msg.role,
        content=f"[anchor: {anchor_id}] {summary}",
        msg_type=MessageType.COMPRESSED_BLOCK,
        version=msg.version + 1,
        metadata={
            **msg.metadata,
            "anchor_id": anchor_id,
            "compressed": True,
            "level": "summarize",
        },
    )

    return StrategyResult(
        message_id=msg.id,
        replacement=replacement,
        record=record,
        anchor=anchor,
        savings=max(savings, 0),
    )


def _discard_strategy(
    candidate: CompressionCandidate,
    msg: Message,
    cfg: CompressorConfig,
) -> StrategyResult:
    """Level 3: Drop completed sub-task messages entirely.

    Only keeps a metadata entry to indicate something was here.
    """
    anchor_id = f"anc_{uuid.uuid4().hex[:10]}"

    record = CompressedRecord(
        anchor_id=anchor_id,
        session_id=msg.metadata.get("session_id", ""),
        original_content=candidate.content,
        summary="[Discarded — completed sub-task]",
        message_ids=(msg.id,),
        metadata={
            "source_type": candidate.message_type.name,
            "level": "discard",
        },
    )

    anchor = AnchorRef(
        anchor_id=anchor_id,
        session_id=msg.metadata.get("session_id", ""),
        source_message_ids=(msg.id,),
        summary="Discarded — completed sub-task context",
        compressed_level=CompressionLevel.LEVEL_3_DISCARD,
    )

    replacement = None  # Fully removed from L1

    return StrategyResult(
        message_id=msg.id,
        replacement=replacement,
        record=record,
        anchor=anchor,
        savings=candidate.token_count,
    )


# ── Strategy chain ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StrategyChain:
    """Ordered set of strategies keyed by ``CompressionLevel``."""

    strategies: dict[CompressionLevel, CompressStrategy] = field(
        default_factory=lambda: {
            CompressionLevel.LEVEL_1_TRUNCATE: _truncate_strategy,
            CompressionLevel.LEVEL_2_SUMMARIZE: _summarize_strategy,
            CompressionLevel.LEVEL_3_DISCARD: _discard_strategy,
        }
    )

    def get(self, level: CompressionLevel) -> CompressStrategy:
        return self.strategies.get(level, _truncate_strategy)


# ── CompressibilityScorer ───────────────────────────────────────────────────


class CompressibilityScorer:
    """Evaluates each message's eligibility for compression.

    Higher score = more eligible (older, larger, less relevant).
    """

    def __init__(self, config: CompressorConfig) -> None:
        self._cfg = config
        self._weights = config.score_weights

    def score(self, messages: list[Message]) -> list[CompressionCandidate]:
        if not messages:
            return []

        now = datetime.now(timezone.utc)
        max_tokens = max((m.token_estimate for m in messages), default=1)

        candidates: list[CompressionCandidate] = []
        for msg in messages:
            if msg.msg_type in (MessageType.SYSTEM, MessageType.USER_INTENT):
                continue  # Never compress these
            if msg.msg_type == MessageType.COMPRESSED_BLOCK:
                continue  # Already compressed
            if msg.token_estimate < self._cfg.min_message_tokens_for_compress:
                continue  # Too small to bother

            # Normalize factors to 0.0–1.0
            age = (now - msg.timestamp).total_seconds()
            age_score = min(age / 3600, 1.0)  # 1 hour → 1.0

            size_score = min(msg.token_estimate / max_tokens, 1.0)

            type_bonus = self._type_bonus(msg.msg_type)

            tool_size = self._tool_result_bonus(msg)

            score = (
                self._weights["age"] * age_score
                + self._weights["size"] * size_score
                + self._weights["type_bonus"] * type_bonus
                + self._weights["tool_result_size"] * tool_size
            )

            level = self._recommend_level(score, msg.token_estimate)

            candidates.append(
                CompressionCandidate(
                    message_id=msg.id,
                    message_type=msg.msg_type,
                    content=msg.content,
                    token_count=msg.token_estimate,
                    score=round(score, 3),
                    recommended_level=level,
                    reason=self._reason(score, level, size_score),
                )
            )

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    @staticmethod
    def _type_bonus(msg_type: MessageType) -> float:
        """Higher bonus = more compressible."""
        bonuses = {
            MessageType.LOG_OUTPUT: 0.9,
            MessageType.DIRECTORY_LISTING: 0.9,
            MessageType.FILE_CONTENT: 0.7,
            MessageType.TOOL_RESULT: 0.6,
            MessageType.SEARCH_RESULT: 0.6,
            MessageType.TOOL_CALL: 0.3,
            MessageType.MODEL_REPLY: 0.2,
            MessageType.USER_INPUT: 0.1,
        }
        return bonuses.get(msg_type, 0.0)

    @staticmethod
    def _tool_result_bonus(msg: Message) -> float:
        if msg.msg_type != MessageType.TOOL_RESULT:
            return 0.0
        # The larger the tool result, the more compressible
        return min(msg.token_estimate / 2000, 1.0)

    @staticmethod
    def _recommend_level(score: float, tokens: int) -> CompressionLevel:
        if score > 0.8 and tokens > 1500:
            return CompressionLevel.LEVEL_3_DISCARD
        if score > 0.6:
            return CompressionLevel.LEVEL_2_SUMMARIZE
        return CompressionLevel.LEVEL_1_TRUNCATE

    @staticmethod
    def _reason(score: float, level: CompressionLevel, size: float) -> str:
        parts = []
        if score > 0.7:
            parts.append("highly compressible")
        elif score > 0.4:
            parts.append("moderately compressible")
        else:
            parts.append("low compressibility")
        parts.append(f"→ {level.name}")
        if size > 0.8:
            parts.append("(large payload)")
        return " ".join(parts)


# ── AsyncCompressor ─────────────────────────────────────────────────────────


class AsyncCompressor:
    """Manages snapshot-isolated compression of the active message list.

    Produces ``CompressionPlan`` objects that the ``ContextManager`` can
    atomically apply at the next safe point.

    Thread safety
    -------------
    * ``submit_plan()`` can be called from any thread.
    * ``collect_plan()`` reads a completed plan; returns ``None`` if none ready.
    """

    def __init__(
        self,
        config: CompressorConfig,
        strategy_chain: StrategyChain | None = None,
    ) -> None:
        self._cfg = config
        self._scorer = CompressibilityScorer(config)
        self._chain = strategy_chain or StrategyChain()
        self._pending_plan: CompressionPlan | None = None
        self._lock = threading.Lock()

    # ── Scoring (synchronous, can run in background thread) ──────────────

    def score(self, messages: list[Message]) -> list[CompressionCandidate]:
        """Score messages — safe to call from any thread (read-only)."""
        return self._scorer.score(messages)

    # ── Plan creation ────────────────────────────────────────────────────

    def create_plan(
        self,
        messages: list[Message],
        trigger: CompressTrigger = CompressTrigger.BUDGET_COMPRESS,
        target_savings: int | None = None,
    ) -> CompressionPlan:
        """Score messages and build a compression plan.

        This does NOT modify ``messages`` — it only computes what *would*
        be compressed.
        """
        candidates = self.score(messages)

        # Select top candidates up to limit
        selected = candidates[: self._cfg.max_candidates_per_round]

        # If target savings given, keep picking until reached
        if target_savings:
            accumulated = 0
            bounded: list[CompressionCandidate] = []
            for c in selected:
                accumulated += c.token_count
                bounded.append(c)
                if accumulated >= target_savings:
                    break
            selected = bounded

        estimated_savings = sum(c.token_count for c in selected)

        return CompressionPlan(
            candidates=tuple(selected),
            trigger=trigger,
            estimated_savings=estimated_savings,
        )

    # ── Async plan submission / collection ───────────────────────────────

    def submit_plan(
        self,
        messages: list[Message],
        trigger: CompressTrigger = CompressTrigger.BUDGET_WARN,
    ) -> None:
        """Submit an async compression job.

        The result is collected via ``collect_plan()``.
        This is safe to call from a background thread.
        """
        plan = self.create_plan(messages, trigger=trigger)
        with self._lock:
            self._pending_plan = plan

    def collect_plan(self) -> CompressionPlan | None:
        """Collect a completed async plan (non-blocking)."""
        with self._lock:
            plan = self._pending_plan
            self._pending_plan = None
        return plan

    # ── Apply plan to produce results ────────────────────────────────────

    def apply_plan(
        self,
        plan: CompressionPlan,
        messages: list[Message],
        session_id: str,
    ) -> ApplyResult:
        """Apply a compression plan to a message list (immutable).

        Returns ``ApplyResult`` with:
        * New message list (compressed candidates replaced / removed)
        * Records to persist to L2
        * Anchors to persist
        * Actual tokens saved
        """
        msg_map = {m.id: m for m in messages}
        kept: list[Message] = []
        records: list[CompressedRecord] = []
        anchors: list[AnchorRef] = []
        total_savings = 0

        for candidate in plan.candidates:
            msg = msg_map.get(candidate.message_id)
            if msg is None:
                continue

            strategy = self._chain.get(candidate.recommended_level)
            result = strategy(candidate, msg, self._cfg)

            if result.record is not None:
                records.append(result.record)
            if result.anchor is not None:
                anchors.append(result.anchor)
            if result.replacement is not None:
                kept.append(result.replacement)
            # else: fully discarded

            total_savings += result.savings

        # Keep messages that weren't compressed
        compressed_ids = {c.message_id for c in plan.candidates}
        for m in messages:
            if m.id not in compressed_ids:
                kept.append(m)

        return ApplyResult(
            messages=tuple(kept),
            records=tuple(records),
            anchors=tuple(anchors),
            savings=total_savings,
        )


# ── ApplyResult ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ApplyResult:
    """Result of applying a ``CompressionPlan`` to a message list."""

    messages: tuple[Message, ...]
    records: tuple[CompressedRecord, ...]
    anchors: tuple[AnchorRef, ...]
    savings: int = 0

    @property
    def message_count(self) -> int:
        return len(self.messages)
