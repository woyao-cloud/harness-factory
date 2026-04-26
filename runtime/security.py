"""Security gate — policy-based tool call approval and sandbox integration."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import auto, Enum
from pathlib import Path
from typing import Any, Callable

from .types import ToolCall

logger = logging.getLogger(__name__)


class Decision(Enum):
    ALLOW = auto()
    DENY = auto()
    ASK = auto()  # Requires user confirmation


@dataclass(frozen=True)
class ApprovalResult:
    decision: Decision = Decision.DENY
    reason: str = ""
    policy_name: str = ""


PolicyFunc = Callable[[ToolCall], ApprovalResult | None]
"""Return ``None`` = no opinion (pass to next policy)."""


# ── Built-in policies ───────────────────────────────────────────────────────


def allow_read_only(tool_call: ToolCall) -> ApprovalResult | None:
    """Allow read-only tools; deny mutating tools."""
    read_tools = {"read", "list", "grep", "glob", "search", "fetch", "view"}
    mutate_tools = {"write", "edit", "delete", "rm", "create", "exec", "bash"}

    if tool_call.tool_name in read_tools:
        return ApprovalResult(Decision.ALLOW, "Read-only tool", "read_only")
    if tool_call.tool_name in mutate_tools:
        return ApprovalResult(Decision.DENY, "Mutation requires approval", "read_only")
    return None


def allow_path_prefix(allowed_prefixes: tuple[str, ...]) -> PolicyFunc:
    """Factory: allow tools operating on files under a specific directory.

    Checks both the raw file_path parameter and its resolved absolute form,
    so relative paths (e.g. ``"research/file.md"``) are correctly matched
    against absolute allowed prefixes (e.g. ``"/home/user/research"``).
    """

    def policy(tool_call: ToolCall) -> ApprovalResult | None:
        raw_path = tool_call.params.get("file_path") or tool_call.params.get("path") or ""
        if not raw_path:
            return None

        # Normalise the path: try to resolve to absolute, fall back to raw
        normalised = raw_path.replace("\\", "/")
        try:
            resolved = str(Path(raw_path).resolve()).replace("\\", "/")
        except Exception:
            resolved = ""

        # Collect candidates: raw form + resolved absolute form (if different)
        candidates = [normalised]
        if resolved and resolved != normalised:
            candidates.append(resolved)

        normalized_raw = raw_path.replace("\\", "/")
        for prefix in allowed_prefixes:
            norm_prefix = prefix.replace("\\", "/")
            if normalized_raw.startswith(norm_prefix):
                return ApprovalResult(Decision.ALLOW, f"Path in {prefix}", "path_prefix")
            for candidate in candidates:
                if candidate.startswith(norm_prefix):
                    return ApprovalResult(Decision.ALLOW, f"Path in {prefix}", "path_prefix")
        return ApprovalResult(
            Decision.DENY, f"Path not in allowed prefixes: {raw_path}", "path_prefix"
        )

    return policy


def deny_commands(patterns: tuple[str, ...]) -> PolicyFunc:
    """Factory: deny tool calls whose params match any regex pattern."""

    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]

    def policy(tool_call: ToolCall) -> ApprovalResult | None:
        raw = str(tool_call.params)
        for pattern, regex in zip(patterns, compiled):
            if regex.search(raw):
                return ApprovalResult(
                    Decision.DENY, f"Matched deny pattern: {pattern}", "deny_commands"
                )
        return None

    return policy


# ── SecurityGate ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SecurityConfig:
    default_decision: Decision = Decision.ASK
    require_approval_for: tuple[str, ...] = ("bash", "exec", "edit", "write")


class SecurityGate:
    """Policy chain for tool call approval.

    Evaluates tool calls against a chain of policies:
    1. First policy to return non-``None`` decides.
    2. If all policies return ``None``, the ``default_decision`` applies.

    Usage::

        gate = SecurityGate()
        gate.add_policy(allow_read_only)
        gate.add_policy(deny_commands(("rm -rf", "drop table")))

        result = gate.approve(tool_call)
        if result.decision is Decision.DENY:
            raise PermissionError(result.reason)
    """

    def __init__(self, config: SecurityConfig | None = None) -> None:
        self._cfg = config or SecurityConfig()
        self._policies: list[PolicyFunc] = []
        self._log: list[ApprovalResult] = []

    def add_policy(self, policy: PolicyFunc) -> None:
        self._policies.append(policy)

    def approve(self, tool_call: ToolCall) -> ApprovalResult:
        """Run the policy chain against a tool call."""
        for policy in self._policies:
            try:
                result = policy(tool_call)
            except Exception as e:
                result = ApprovalResult(
                    Decision.DENY, f"Policy error: {e}", "error"
                )
            if result is not None:
                self._log.append(result)
                return result

        # Default decision
        if tool_call.tool_name in self._cfg.require_approval_for:
            result = ApprovalResult(
                self._cfg.default_decision,
                f"Tool '{tool_call.tool_name}' requires approval",
                "default",
            )
        else:
            result = ApprovalResult(Decision.ALLOW, "No policy restriction", "default")

        self._log.append(result)
        return result

    @property
    def audit_log(self) -> tuple[ApprovalResult, ...]:
        return tuple(self._log)
