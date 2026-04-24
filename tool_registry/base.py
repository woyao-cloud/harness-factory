"""Base tool protocol — abstract tool definition + result types for the Tool Registry."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Tool result ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolResult:
    """Structured result from any tool execution.

    The ``text`` field is what the LLM sees; ``data`` carries structured
    information for programmatic consumers.
    """

    success: bool = True
    text: str = ""
    data: Any = None
    error: str | None = None
    mime_type: str = "text/plain"

    @classmethod
    def ok(cls, text: str = "", data: Any = None) -> ToolResult:
        return cls(success=True, text=text, data=data)

    @classmethod
    def err(cls, error: str) -> ToolResult:
        return cls(success=False, text=f"Error: {error}", error=error)

    def __bool__(self) -> bool:
        return self.success


# ── Tool parameter descriptor ────────────────────────────────────────────────


@dataclass(frozen=True)
class ParamSpec:
    """Parameter specification for tool definitions."""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None

    def to_runtime_param(self) -> "ToolParam":  # noqa: F821
        from runtime.types import ToolParam as _TP
        return _TP(
            name=self.name,
            type=self.type,
            description=self.description,
            required=self.required,
        )


# ── Base tool ────────────────────────────────────────────────────────────────


class BaseTool(ABC):
    """Abstract base for all tools in the registry.

    Subclasses must provide:
    * ``name`` — unique tool identifier
    * ``description`` — what the tool does (for the LLM)
    * ``parameters`` — parameter schema
    * ``execute()`` — implementation

    Usage::

        class MyTool(BaseTool):
            name = "my_tool"
            description = "Does something useful"
            parameters = (ParamSpec("input", description="the input"),)

            async def execute(self, input: str) -> ToolResult: ...
    """

    name: str = ""
    description: str = ""
    parameters: tuple[ParamSpec, ...] = ()

    @property
    def definition(self) -> "ToolDefinition":  # noqa: F821
        """Convert to runtime ``ToolDefinition`` for pipeline registration."""
        from runtime.types import ToolDefinition as _TD, ToolParam as _TP

        params = tuple(
            _TP(
                name=p.name,
                type=p.type,
                description=p.description,
                required=p.required,
            )
            for p in self.parameters
        )
        return _TD(name=self.name, description=self.description, parameters=params)

    @abstractmethod
    async def execute(self, **params: Any) -> ToolResult:
        """Execute the tool with given parameters.

        Must return a ``ToolResult`` — never raise on expected errors.
        """
        ...

    # ── Error-safe wrapper for integration with pipeline ─────────────────

    async def safe_execute(self, **params: Any) -> str:
        """Wrapper for the pipeline executor — catches all errors.

        The pipeline expects an ``executor(**params) -> Any`` signature.
        This method converts structured ``ToolResult`` to the string
        that the pipeline records in the conversation.
        """
        try:
            result = await self.execute(**params)
            return str(result.data) if result.data else result.text
        except Exception as exc:
            logger.exception("Tool %s failed", self.name)
            return f"Error: {self.name} failed — {exc}"

    def to_anthropic_tool(self) -> dict:
        return self.definition.to_anthropic_tool()

    def to_openai_tool(self) -> dict:
        return self.definition.to_openai_tool()
