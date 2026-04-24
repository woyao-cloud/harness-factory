"""Layered storage for compressed records — L2 (SQLite) and L3 (remote interface).

L1 (active messages) is owned by the ContextManager directly since it is the
current working set of the inference loop.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .types import (
    AnchorRef,
    CompressedRecord,
    CompressionLevel,
    RestoredSnippet,
    StoreLevel,
)


# ── L3 remote-store protocol ─────────────────────────────────────────────────


class RemoteStore(Protocol):
    """Interface for L3 remote/object-storage backends."""

    async def write(self, record: CompressedRecord) -> None: ...

    async def read(self, anchor_id: str) -> CompressedRecord | None: ...

    async def delete(self, anchor_id: str) -> None: ...

    async def list_by_session(self, session_id: str) -> list[CompressedRecord]: ...


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StoreConfig:
    db_path: str | Path = "context_store.sqlite"
    l3_store: RemoteStore | None = None
    auto_init: bool = True


# ── Helpers ──────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── LayeredStore ─────────────────────────────────────────────────────────────


class LayeredStore:
    """L2 (SQLite) + L3 (remote) persistent storage for compressed records.

    Thread-safe for SQLite access.  L3 writes are fire-and-forget — failures
    are logged but never bubble up to the caller.
    """

    def __init__(self, config: StoreConfig) -> None:
        self._config = config
        self._l3 = config.l3_store
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        if config.auto_init:
            self._init_db()

    @property
    def store_path(self) -> str:
        return str(self._config.db_path)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        db_path = Path(self._config.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS compressed_records (
                record_id    TEXT PRIMARY KEY,
                anchor_id    TEXT NOT NULL,
                session_id   TEXT NOT NULL,
                store_level  TEXT NOT NULL DEFAULT 'l2_local',
                content      TEXT NOT NULL,
                summary      TEXT NOT NULL DEFAULT '',
                metadata     TEXT NOT NULL DEFAULT '{}',
                compressed_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_cr_anchor
                ON compressed_records(session_id, anchor_id);
            CREATE INDEX IF NOT EXISTS idx_cr_session
                ON compressed_records(session_id);

            CREATE TABLE IF NOT EXISTS anchors (
                anchor_id          TEXT PRIMARY KEY,
                session_id         TEXT NOT NULL,
                source_path        TEXT,
                source_message_ids TEXT NOT NULL DEFAULT '[]',
                summary            TEXT NOT NULL DEFAULT '',
                compressed_level   TEXT NOT NULL DEFAULT 'level_1_truncate',
                key_terms          TEXT NOT NULL DEFAULT '',
                metadata           TEXT NOT NULL DEFAULT '{}',
                created_at         TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_anchor_source
                ON anchors(session_id, source_path);
            CREATE INDEX IF NOT EXISTS idx_anchor_session
                ON anchors(session_id);
        """)
        self._db.commit()

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    # ── Write ─────────────────────────────────────────────────────────────

    def write(self, record: CompressedRecord) -> None:
        """Write a record to L2 (and fan-out to L3 if configured)."""
        self._write_l2(record)
        self._write_l3(record)

    def write_anchor(self, anchor: AnchorRef) -> None:
        """Store an anchor reference pointing to compressed content."""
        if self._db is None:
            self._init_db()
        with self._lock:
            self._db.execute(
                """
                INSERT OR REPLACE INTO anchors
                    (anchor_id, session_id, source_path,
                     source_message_ids, summary, compressed_level,
                     key_terms, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    anchor.anchor_id,
                    anchor.session_id,
                    anchor.source_path,
                    json.dumps(list(anchor.source_message_ids)),
                    anchor.summary,
                    anchor.compressed_level.name,
                    json.dumps(
                        anchor.metadata.get("key_terms", [])
                    ),
                    json.dumps(anchor.metadata),
                    _now(),
                ),
            )
            self._db.commit()

    def _write_l2(self, record: CompressedRecord) -> None:
        if self._db is None:
            self._init_db()
        with self._lock:
            self._db.execute(
                """
                INSERT OR REPLACE INTO compressed_records
                    (record_id, anchor_id, session_id, store_level,
                     content, summary, metadata, compressed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.record_id,
                    record.anchor_id,
                    record.session_id,
                    record.store_level.value,
                    record.original_content,
                    record.summary,
                    json.dumps(record.metadata),
                    _now(),
                ),
            )
            self._db.commit()

    def _write_l3(self, record: CompressedRecord) -> None:
        if self._l3 is not None:
            import asyncio

            try:
                asyncio.ensure_future(self._l3.write(record))
            except Exception:
                pass  # L3 failures are non-fatal

    # ── Read ──────────────────────────────────────────────────────────────

    def read(
        self, anchor_id: str, prefer_level: StoreLevel = StoreLevel.L2_LOCAL
    ) -> CompressedRecord | None:
        """Read a compressed record, preferring the specified tier."""
        if prefer_level is StoreLevel.L3_REMOTE and self._l3 is not None:
            try:
                import asyncio

                coro = self._l3.read(anchor_id)
                try:
                    loop = asyncio.get_running_loop()
                    future = asyncio.run_coroutine_threadsafe(coro, loop)
                    result = future.result(timeout=5)
                    if result is not None:
                        return result
                except RuntimeError:
                    pass  # no running loop — skip L3
            except Exception:
                pass

        return self._read_l2(anchor_id)

    def _read_l2(self, anchor_id: str) -> CompressedRecord | None:
        if self._db is None:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM compressed_records WHERE anchor_id = ?",
                (anchor_id,),
            ).fetchone()
        if row is None:
            return None
        return CompressedRecord(
            record_id=row["record_id"],
            anchor_id=row["anchor_id"],
            session_id=row["session_id"],
            store_level=StoreLevel(row["store_level"]),
            original_content=row["content"],
            summary=row["summary"],
            metadata=json.loads(row["metadata"]),
        )

    def read_anchor(self, anchor_id: str) -> AnchorRef | None:
        """Look up an anchor by ID."""
        if self._db is None:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM anchors WHERE anchor_id = ?",
                (anchor_id,),
            ).fetchone()
        if row is None:
            return None
        return AnchorRef(
            anchor_id=row["anchor_id"],
            session_id=row["session_id"],
            source_path=row["source_path"],
            source_message_ids=tuple(json.loads(row["source_message_ids"])),
            summary=row["summary"],
            compressed_level=CompressionLevel[row["compressed_level"]],
            metadata=json.loads(row["metadata"]),
        )

    # ── Snippet restoration ──────────────────────────────────────────────

    def restore_snippet(
        self,
        anchor_id: str,
        query: str = "",
        max_tokens: int = 2000,
    ) -> RestoredSnippet | None:
        """Restore a targeted snippet from a compressed record.

        ``query`` can be:
            * ``"lines 48-52"`` — line-range extraction
            * ``"function foo"`` — keyword-based extraction
            * ``""`` — return first ``max_tokens`` chars of content
        """
        record = self.read(anchor_id)
        if record is None:
            return None

        content = record.original_content
        total_tokens = len(content) // 4
        source_path = record.metadata.get("source_path")

        extracted = self._extract_snippet(content, query, max_tokens)
        restored_tokens = len(extracted) // 4

        return RestoredSnippet(
            anchor_id=anchor_id,
            content=extracted,
            source_path=source_path,
            truncated=restored_tokens < total_tokens,
            total_original_tokens=total_tokens,
            restored_tokens=restored_tokens,
        )

    @staticmethod
    def _extract_snippet(content: str, query: str, max_chars: int) -> str:
        max_chars = max_chars * 4  # convert tokens → chars
        if not query:
            return content[:max_chars]

        # Line-range query
        if query.startswith("lines ") or query.startswith("line "):
            import re

            m = re.match(r"lines?\s+(\d+)(?:\s*-\s*(\d+))?", query)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else start
                lines = content.splitlines()
                selected = lines[start - 1 : end]  # 1-based
                result = "\n".join(selected)
                return result[:max_chars]

        # Keyword-based: find surrounding context
        keywords = query.lower().split()
        lines = content.splitlines()
        matched_indices: set[int] = set()
        for i, line in enumerate(lines):
            if any(kw in line.lower() for kw in keywords):
                start = max(0, i - 3)
                end = min(len(lines), i + 4)
                matched_indices.update(range(start, end))

        if matched_indices:
            sorted_idx = sorted(matched_indices)
            result_lines: list[str] = []
            prev = -1
            for idx in sorted_idx:
                if prev >= 0 and idx > prev + 1:
                    result_lines.append("...")
                result_lines.append(lines[idx])
                prev = idx
            result = "\n".join(result_lines)
            return result[:max_chars]

        # Fallback: first N chars
        return content[:max_chars]

    # ── Cross-session lookups ────────────────────────────────────────────

    def cross_session_lookup(
        self,
        source_path: str,
        session_id: str | None = None,
    ) -> list[AnchorRef]:
        """Find all anchors referencing a given source path.

        Optionally scoped to a specific session.
        """
        if self._db is None:
            return []
        with self._lock:
            if session_id:
                rows = self._db.execute(
                    "SELECT * FROM anchors WHERE source_path = ? AND session_id = ?",
                    (source_path, session_id),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM anchors WHERE source_path = ?",
                    (source_path,),
                ).fetchall()
        return [self._row_to_anchor(r) for r in rows]

    def list_session_anchors(self, session_id: str) -> list[AnchorRef]:
        """All anchors for a session."""
        if self._db is None:
            return []
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM anchors WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [self._row_to_anchor(r) for r in rows]

    def list_session_records(self, session_id: str) -> list[CompressedRecord]:
        """All compressed records for a session."""
        if self._db is None:
            return []
        with self._lock:
            rows = self._db.execute(
                """SELECT * FROM compressed_records
                   WHERE session_id = ? ORDER BY compressed_at""",
                (session_id,),
            ).fetchall()
        return [
            CompressedRecord(
                record_id=r["record_id"],
                anchor_id=r["anchor_id"],
                session_id=r["session_id"],
                store_level=StoreLevel(r["store_level"]),
                original_content=r["content"],
                summary=r["summary"],
                metadata=json.loads(r["metadata"]),
            )
            for r in rows
        ]

    # ── Cleanup ──────────────────────────────────────────────────────────

    def delete_session(self, session_id: str) -> None:
        """Remove all records and anchors for a session."""
        if self._db is None:
            return
        with self._lock:
            self._db.execute(
                "DELETE FROM compressed_records WHERE session_id = ?",
                (session_id,),
            )
            self._db.execute(
                "DELETE FROM anchors WHERE session_id = ?",
                (session_id,),
            )
            self._db.commit()

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_anchor(row: sqlite3.Row) -> AnchorRef:
        return AnchorRef(
            anchor_id=row["anchor_id"],
            session_id=row["session_id"],
            source_path=row["source_path"],
            source_message_ids=tuple(json.loads(row["source_message_ids"])),
            summary=row["summary"],
            compressed_level=CompressionLevel[row["compressed_level"]],
            metadata=json.loads(row["metadata"]),
        )
