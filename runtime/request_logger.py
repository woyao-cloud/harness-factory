"""LLM request logger — dump full request payloads to ``log/`` directory.

Each inference call is saved as a JSON file named
``{session_id}_{timestamp}.json`` for debugging and audit.

Usage::

    logger = LLMRequestLogger()
    logger.log(
        session_id="sess_20260426_123456",
        model="claude-sonnet-4",
        system_prompt="You are a helpful assistant",
        messages=[{"role": "user", "content": "Hello"}],
        tools=[{"name": "web_search", ...}],
    )
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class LLMRequestLogger:
    """Save LLM request payloads to ``log/`` for debugging.

    Each request is written as a separate JSON file.  Old logs are
    automatically pruned when the total exceeds ``max_files``.
    """

    def __init__(self, log_dir: str = "log", max_files: int = 200) -> None:
        self._log_dir = Path(log_dir)
        self._max_files = max_files
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        session_id: str,
        model: str,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> str | None:
        """Write a request dump to ``log/{session_id}_{timestamp}.json``.

        Returns the file path written, or ``None`` on failure.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:23]
        # _{timestamp}
        filename = f"{session_id}.json"
        path = self._log_dir / filename

        payload: dict[str, Any] = {
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "system_prompt": system_prompt,
            "messages": messages,
            "tools": tools,
        }

        try:
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            self._prune_old()
            return str(path)
        except OSError as exc:
            logger.warning("Failed to write LLM request log to %s: %s", path, exc)
            return None

    def _prune_old(self) -> None:
        """Remove oldest log files when count exceeds ``max_files``."""
        try:
            files = sorted(self._log_dir.iterdir(), key=lambda p: p.stat().st_mtime)
            while len(files) > self._max_files:
                files[0].unlink(missing_ok=True)
                files = files[1:]
        except OSError as exc:
            logger.warning("Failed to prune old request logs: %s", exc)
