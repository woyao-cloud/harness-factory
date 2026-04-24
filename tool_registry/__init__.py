"""Tool Registry — define, register, discover, and execute tools.

Components::

    BaseTool         — abstract base with name/description/parameters/execute
    ToolResult       — structured result (ok/err pattern)
    ParamSpec        — parameter schema descriptor
    ToolRegistry     — central registry (register / get / list / execute / install)

Built-in tool modules::

    file_tools       — read, write, edit, glob, grep
    web_tools        — web_search, web_fetch
"""

from .base import BaseTool, ParamSpec, ToolResult
from .registry import ToolRegistry

from .file_tools import (
    EditTool,
    GlobTool,
    GrepTool,
    ReadTool,
    WriteTool,
)

from .web_tools import (
    WebFetchTool,
    WebSearchTool,
)

__all__ = [
    # Core
    "BaseTool",
    "ToolResult",
    "ParamSpec",
    "ToolRegistry",
    # File tools
    "ReadTool",
    "WriteTool",
    "EditTool",
    "GlobTool",
    "GrepTool",
    # Web tools
    "WebSearchTool",
    "WebFetchTool",
]
