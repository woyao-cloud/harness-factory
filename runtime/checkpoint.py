"""Checkpoint — save/restore session state for crash recovery."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from context_manager import Message, MessageType
from .types import HarnessSpec, PipelineEvent, EventPayload
from .message_bus import EventHandler

logger = logging.getLogger(__name__)

CHECKPOINT_VERSION = 1


# ── Serializable snapshot ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Checkpoint:
    """Full serializable snapshot of a session at a point in time."""

    version: int = CHECKPOINT_VERSION
    session_id: str = ""
    created_at: str = ""
    turn_count: int = 0
    messages: tuple[dict, ...] = ()  # serialized Message dicts
    harness_spec: dict | None = None
    config: dict | None = None
    store_path: str = ""  # ContextManager L2 SQLite path for cross-session anchor persistence
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Filesystem store ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckpointConfig:
    save_dir: str | Path = "checkpoints"
    auto_save_interval: int = 30  # seconds
    max_checkpoints_per_session: int = 10
    compress: bool = False  # Future: gzip compression


class CheckpointStore:
    """Filesystem-based checkpoint persistence.

    Directory layout::

        {save_dir}/
        ├── {session_id}/
        │   ├── checkpoint_20250424_120000.json
        │   ├── checkpoint_20250424_120030.json
        │   └── meta.json
        └── latest/  →  symlink to {session_id}/
    """

    def __init__(self, config: CheckpointConfig) -> None:
        self._cfg = config
        self._save_dir = Path(config.save_dir)
        self._save_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ── Save ─────────────────────────────────────────────────────────────

    def save(self, checkpoint: Checkpoint) -> Path:
        """Persist a checkpoint to disk. Returns the file path."""
        session_dir = self._session_dir(checkpoint.session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        filename = f"checkpoint_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}.json"
        filepath = session_dir / filename

        data = asdict(checkpoint)
        data["_version"] = CHECKPOINT_VERSION

        with self._lock:
            # Prune old checkpoints
            self._prune(session_dir)
            # Write
            filepath.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            # Update meta
            self._write_meta(checkpoint.session_id, filename)

        # Trim old files
        self._trim(session_dir)

        logger.debug("Checkpoint saved: %s", filepath)
        return filepath

    def _session_dir(self, session_id: str) -> Path:
        return self._save_dir / session_id

    def _prune(self, session_dir: Path) -> None:
        """Remove excess checkpoints beyond max."""
        if not session_dir.exists():
            return
        files = sorted(
            [f for f in session_dir.iterdir() if f.name.startswith("checkpoint_")],
            reverse=True,
        )
        for f in files[self._cfg.max_checkpoints_per_session - 1:]:
            f.unlink(missing_ok=True)

    def _trim(self, session_dir: Path) -> None:
        """Remove excess checkpoint files beyond max."""
        if not session_dir.exists():
            return
        files = sorted(
            [f for f in session_dir.iterdir() if f.suffix == ".json" and f.name != "meta.json"],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        for f in files[self._cfg.max_checkpoints_per_session:]:
            f.unlink(missing_ok=True)

    def _write_meta(self, session_id: str, latest: str) -> None:
        meta = self._session_dir(session_id) / "meta.json"
        meta.write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "latest": latest,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # ── Load ─────────────────────────────────────────────────────────────

    def load_latest(self, session_id: str) -> Checkpoint | None:
        """Load the most recent checkpoint for a session."""
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return None

        meta_path = session_dir / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                latest = meta.get("latest", "")
                if latest:
                    cp_path = session_dir / latest
                    if cp_path.exists():
                        return self._load_file(cp_path)
            except (json.JSONDecodeError, KeyError):
                pass

        # Fallback: find newest file
        files = sorted(
            [f for f in session_dir.iterdir() if f.suffix == ".json" and f.name != "meta.json"],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if files:
            return self._load_file(files[0])
        return None

    def _load_file(self, path: Path) -> Checkpoint | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop("_version", None)
            return Checkpoint(**data)
        except Exception as exc:
            logger.warning("Failed to load checkpoint %s: %s", path, exc)
            return None

    def list_checkpoints(self, session_id: str) -> list[dict]:
        """List all checkpoints for a session (metadata only, no messages)."""
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return []
        result = []
        for f in sorted(session_dir.iterdir()):
            if f.suffix != ".json" or f.name == "meta.json":
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                result.append({
                    "file": f.name,
                    "created_at": data.get("created_at", ""),
                    "turn_count": data.get("turn_count", 0),
                    "message_count": len(data.get("messages", [])),
                })
            except Exception:
                continue
        return result

    # ── Cleanup ──────────────────────────────────────────────────────────

    def delete_session(self, session_id: str) -> None:
        """Remove all checkpoints for a session."""
        session_dir = self._session_dir(session_id)
        if session_dir.exists():
            import shutil
            shutil.rmtree(session_dir)
            logger.info("Checkpoints deleted for session %s", session_id)


# ── Auto-save manager ────────────────────────────────────────────────────────


class AutoSaveManager:
    """Periodically saves checkpoints by listening to pipeline events.

    Attach to a ``MessageBus`` and it will save after each pipeline turn
    and periodically on a timer.
    """

    def __init__(
        self,
        store: CheckpointStore,
        interval: int = 30,
    ) -> None:
        self._store = store
        self._interval = interval
        self._last_save: float = 0.0
        self._current_checkpoint: Checkpoint | None = None

    def create_handler(self) -> EventHandler:
        """Return an ``EventHandler`` to attach to ``MessageBus.on()``."""

        def handler(payload: EventPayload) -> None:
            if payload.event in (
                PipelineEvent.PIPELINE_TURN,
                PipelineEvent.SESSION_CLOSED,
            ):
                self._request_save()

        return handler

    def _request_save(self) -> None:
        now = time.time()
        if now - self._last_save >= self._interval:
            self.save()
            self._last_save = now

    def save(self) -> None:
        if self._current_checkpoint is None:
            return
        self._store.save(self._current_checkpoint)

    def update_checkpoint(
        self,
        session_id: str,
        turn_count: int,
        messages: list[Message],
        spec: HarnessSpec | None = None,
        config: dict | None = None,
        store_path: str = "",
    ) -> None:
        """Update the pending checkpoint data without writing to disk."""
        self._current_checkpoint = Checkpoint(
            session_id=session_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            turn_count=turn_count,
            messages=tuple(
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "msg_type": m.msg_type.name,
                    "version": m.version,
                    "metadata": m.metadata,
                }
                for m in messages
            ),
            harness_spec=asdict(spec) if spec else None,
            store_path=store_path,
            config=config,
        )
