"""ToolRegistry — register, discover, and execute tools.

One-step install into any ``HarnessRuntime``::

    registry = ToolRegistry()
    registry.install_all(harness)
    # or selectively:
    registry.register(ReadTool())
    registry.install(harness, names=["read", "grep"])
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BaseTool, ParamSpec, ToolResult

logger = logging.getLogger(__name__)


class _AliasTool(BaseTool):
    """Wrapper that presents a source tool under a different name.

    Used internally by ``ToolRegistry.alias()`` to register the same
    executor under multiple names while keeping tool definitions correct
    (the alias appears with its own name in tool definitions).
    """

    def __init__(self, source: BaseTool, alias_name: str) -> None:
        self._source = source
        self._alias_name = alias_name

    # ── Override identity ───────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._alias_name

    @property
    def description(self) -> str:
        return self._source.description

    @property
    def parameters(self) -> tuple[ParamSpec, ...]:
        return self._source.parameters

    @property
    def definition(self) -> "ToolDefinition":  # noqa: F821
        """Alias definition — uses the alias name, not the source name."""
        from runtime.types import ToolDefinition as _TD, ToolParam as _TP

        params = tuple(
            _TP(name=p.name, type=p.type, description=p.description, required=p.required)
            for p in self._source.parameters
        )
        return _TD(name=self._alias_name, description=self._source.description, parameters=params)

    # ── Delegate execution ──────────────────────────────────────────

    async def execute(self, **params: Any) -> ToolResult:
        return await self._source.execute(**params)

    async def safe_execute(self, **params: Any) -> str:
        return await self._source.safe_execute(**params)


class ToolRegistry:
    """Central registry for tool discovery and execution.

    Lifecycle::

        registry = ToolRegistry()
        registry.register(ReadTool())
        registry.register(WriteTool())

        for tool in registry.list():
            print(tool.name, tool.description)

        result = await registry.execute("read", path="file.txt")
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    # ── Registration ─────────────────────────────────────────────────────

    def register(self, tool: BaseTool) -> None:
        """Register a tool by its ``name``."""
        if not tool.name:
            raise ValueError("Tool must have a non-empty name")
        if tool.name in self._tools:
            logger.warning("Overwriting tool '%s'", tool.name)
        self._tools[tool.name] = tool

    def register_all(self, tools: list[BaseTool]) -> None:
        for t in tools:
            self.register(t)

    def alias(self, new_name: str, existing_name: str) -> None:
        """Create an alias — register the same tool under an additional name.

        Useful when models generate variant tool names (e.g. ``"search"``
        instead of ``"web_search"``).
        """
        source = self._tools.get(existing_name)
        if source is None:
            raise ValueError(
                f"Cannot alias '{new_name}': source tool '{existing_name}' "
                f"not found in registry"
            )
        if new_name in self._tools:
            logger.warning("Overwriting tool '%s' with alias of '%s'", new_name, existing_name)
        self._tools[new_name] = _AliasTool(source, new_name)

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    # ── Discovery ────────────────────────────────────────────────────────

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list(self) -> list[BaseTool]:
        return list(self._tools.values())

    def list_by_category(self, category: str) -> list[BaseTool]:
        """Filter tools by category tag in definition metadata.

        Category is matched against ``tool.definition.name`` prefix
        (e.g. ``"file_reader"`` matches ``"file_"``).
        """
        return [t for t in self._tools.values() if category in t.name]

    def definitions(self) -> list["ToolDefinition"]:  # noqa: F821
        """All tool definitions for LLM function calling."""
        from runtime.types import ToolDefinition as _TD
        return [t.definition for t in self._tools.values()]

    @property
    def count(self) -> int:
        return len(self._tools)

    # ── Execution ────────────────────────────────────────────────────────

    async def execute(self, name: str, **params: Any) -> ToolResult:
        """Execute a tool by name."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.err(f"Tool '{name}' not found")
        return await tool.execute(**params)

    async def safe_execute(self, name: str, **params: Any) -> str:
        """Execute and return a string safe for the conversation history.

        This is the interface expected by ``Pipeline`` executors.
        """
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: Tool '{name}' not found"
        return await tool.safe_execute(**params)

    # ── Harness integration ──────────────────────────────────────────────

    def install(
        self,
        harness: "HarnessRuntime",  # noqa: F821
        names: list[str] | None = None,
    ) -> None:
        """Register all (or selected) tools into a ``HarnessRuntime``.

        Each tool is registered with its ``safe_execute`` method as the
        executor, so return values are automatically converted to strings
        safe for the conversation history.
        """
        tools = (
            [self._tools[n] for n in names if n in self._tools]
            if names is not None
            else list(self._tools.values())
        )
        for tool in tools:
            harness.register_tool(tool.definition, tool.safe_execute)

    def install_all(self, harness: "HarnessRuntime") -> None:  # noqa: F821
        """Install every registered tool into the harness."""
        self.install(harness)
