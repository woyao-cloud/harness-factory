"""Shared utilities for harness factories.

Provides helper functions to reduce boilerplate when composing tools,
security policies, and specs into domain-specific harnesses.
"""

from __future__ import annotations

from pathlib import Path

from runtime.security import allow_path_prefix, allow_read_only
from tool_registry import (
    GlobTool,
    GrepTool,
    ReadTool,
    ToolRegistry,
    WebFetchTool,
    WebSearchTool,
    WriteTool,
)


def install_file_tools(
    registry: ToolRegistry,
    allowed_roots: tuple[str, ...],
) -> None:
    """Register Read, Write, Glob, Grep tools scoped to ``allowed_roots``."""
    registry.register(ReadTool(allowed_roots=allowed_roots))
    registry.register(WriteTool(allowed_roots=allowed_roots))
    registry.register(GlobTool(allowed_roots=allowed_roots))
    registry.register(GrepTool(allowed_roots=allowed_roots))


def install_web_tools(registry: ToolRegistry) -> None:
    """Register WebSearch and WebFetch tools."""
    registry.register(WebSearchTool())
    registry.register(WebFetchTool())


def read_only_path_policy(allowed_roots: tuple[str, ...]) -> tuple:
    """Return security policies that allow reads on roots, deny writes elsewhere.

    Returns (allow_read_only, *path_policies) — a tuple suitable for
    ``add_policy()`` on ``SecurityGate``.
    """
    policies = [allow_read_only]
    for root in allowed_roots:
        policies.append(allow_path_prefix((str(Path(root).resolve()),)))
    return tuple(policies)


def ensure_dir(path: str | Path) -> Path:
    """Resolve and ensure a directory exists, returning the resolved Path."""
    p = Path(path).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p
