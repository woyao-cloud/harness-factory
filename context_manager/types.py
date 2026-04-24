"""Core types for the ContextManager system."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any


# ── Enums ────────────────────────────────────────────────────────────────────


class StoreLevel(Enum):
    """Storage tier for compressed records."""

    L1_MEMORY = "l1_memory"
    L2_LOCAL = "l2_local"
    L3_REMOTE = "l3_remote"


class CompressionLevel(Enum):
    """Compression aggressiveness levels."""

    LEVEL_0_PRESERVE = auto()  # No compression, keep as-is
    LEVEL_1_TRUNCATE = auto()  # Truncate long outputs
    LEVEL_2_SUMMARIZE = auto()  # LLM-generated summary
    LEVEL_3_DISCARD = auto()  # Drop completed sub-task context
    LEVEL_4_STRUCTURED = auto()  # Full dialog → key decision nodes


class CompressTrigger(Enum):
    """What triggered the compression."""

    BUDGET_WARN = auto()  # Async pre-compression at 65%
    BUDGET_COMPRESS = auto()  # Active compression at 80%
    BUDGET_CRITICAL = auto()  # Aggressive compression at 92%
    HARD_LIMIT = auto()  # Emergency protocol at 98%
    MANUAL = auto()  # Explicit request


class MessageType(Enum):
    """Classification of a message for compression decisions."""

    SYSTEM = auto()
    USER_INPUT = auto()
    USER_INTENT = auto()
    MODEL_REPLY = auto()
    TOOL_CALL = auto()
    TOOL_RESULT = auto()
    FILE_CONTENT = auto()
    LOG_OUTPUT = auto()
    DIRECTORY_LISTING = auto()
    SEARCH_RESULT = auto()
    COMPRESSED_BLOCK = auto()
    SUPPLEMENTAL = auto()


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Message:
    """A single turn in the conversation history.

    Frozen to enforce immutability — use ``replace()`` to create modified copies.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    role: str = "user"  # user | assistant | tool_result | system
    content: str = ""
    msg_type: MessageType = MessageType.USER_INPUT
    version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def token_estimate(self) -> int:
        """Rough token count (4 chars ≈ 1 token)."""
        return len(self.content) // 4


@dataclass(frozen=True)
class AnchorRef:
    """An anchor pointing to compressed content in L2/L3 storage.

    The anchor replaces original content in L1 after compression.
    """

    anchor_id: str
    session_id: str
    source_path: str | None = None
    source_message_ids: tuple[str, ...] = ()
    summary: str = ""
    compressed_level: CompressionLevel = CompressionLevel.LEVEL_1_TRUNCATE
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class CompressedRecord:
    """Full record stored in L2/L3 with original content + anchor metadata."""

    record_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    anchor_id: str = ""
    session_id: str = ""
    store_level: StoreLevel = StoreLevel.L2_LOCAL
    original_content: str = ""
    summary: str = ""
    message_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    compressed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class CompressionCandidate:
    """A message evaluated for compression with its score."""

    message_id: str
    message_type: MessageType
    content: str
    token_count: int
    score: float  # Higher = more eligible for compression
    recommended_level: CompressionLevel = CompressionLevel.LEVEL_1_TRUNCATE
    reason: str = ""


@dataclass(frozen=True)
class CompressionPlan:
    """A set of messages to compress and how."""

    candidates: tuple[CompressionCandidate, ...]
    trigger: CompressTrigger = CompressTrigger.BUDGET_COMPRESS
    estimated_savings: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class BudgetState:
    """Snapshot of the current budget tracking state."""

    current_usage: int = 0
    total_budget: int = 0
    ratio: float = 0.0
    trigger: CompressTrigger | None = None

    @property
    def remaining(self) -> int:
        return self.total_budget - self.current_usage


@dataclass(frozen=True)
class ToolCall:
    """Represents a tool invocation parsed from LLM output."""

    tool_name: str
    params: dict[str, Any] = field(default_factory=dict)
    raw_params: str = ""


@dataclass(frozen=True)
class DependencyHit:
    """A detected dependency between a tool call and compressed content."""

    anchor_id: str
    confidence: float  # 0.0 - 1.0
    match_type: str  # file_path | line_number | key_term | semantic
    context_request: str = ""  # What specifically to restore


@dataclass(frozen=True)
class RestorationRequest:
    """Request to restore content for a dependency hit."""

    anchor_id: str
    context_request: str
    max_tokens: int = 2000


@dataclass(frozen=True)
class RestoredSnippet:
    """Restored content ready for injection."""

    anchor_id: str
    content: str
    source_path: str | None = None
    truncated: bool = False
    total_original_tokens: int = 0
    restored_tokens: int = 0

    def format_injection(self) -> str:
        parts = [f"[Restored: {self.source_path or 'content'}]"]
        parts.append(self.content)
        if self.truncated:
            parts.append(
                f"[{self.restored_tokens}/{self.total_original_tokens} tokens restored; "
                f"use a targeted request for full detail]"
            )
        return "\n".join(parts)
