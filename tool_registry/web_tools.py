"""Built-in web tools — search and fetch.

All tools include:
* Timeout and size limits
* Graceful degraded mode when network backends unavailable
* Structured ``ToolResult`` responses
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BaseTool, ParamSpec, ToolResult

logger = logging.getLogger(__name__)


# ── Backend detection (lazy) ────────────────────────────────────────────────


def _resolve_http_backend() -> str:
    """Return the name of the available HTTP backend or 'none'."""
    for mod in ("httpx", "requests", "urllib.request"):
        try:
            __import__(mod)
            return mod
        except ImportError:
            continue
    return "none"


def _http_get(url: str, timeout: float = 15.0) -> tuple[int, str]:
    """Make a GET request using whichever backend is available."""
    backend = _resolve_http_backend()

    if backend == "httpx":
        import httpx
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "HarnessFactory/1.0"})
            resp.raise_for_status()
            return resp.status_code, resp.text

    elif backend == "requests":
        import requests
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "HarnessFactory/1.0"})
        resp.raise_for_status()
        return resp.status_code, resp.text

    elif backend == "urllib.request":
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "HarnessFactory/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8", errors="replace")
            return resp.status, content

    raise RuntimeError("No HTTP backend available")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. WebSearch
# ═══════════════════════════════════════════════════════════════════════════════


class WebSearchTool(BaseTool):
    """Search the web for information using a query string."""

    name = "web_search"
    description = "Search the web for up-to-date information"
    parameters = (
        ParamSpec("query", description="Search query string"),
        ParamSpec(
            "max_results",
            type="integer",
            description="Maximum number of results (1-20)",
            required=False,
        ),
        ParamSpec(
            "engine",
            description="Search engine to use (web, news, scholar)",
            required=False,
        ),
    )

    def __init__(self, default_engine: str = "web", max_results: int = 5) -> None:
        self._default_engine = default_engine
        self._max_results = max_results

    async def execute(
        self,
        query: str,
        max_results: int | None = None,
        engine: str | None = None,
    ) -> ToolResult:
        limit = min(max_results or self._max_results, 20)
        engine_name = engine or self._default_engine

        backend = _resolve_http_backend()
        if backend == "none":
            return ToolResult.ok(
                text=(
                    f"[WebSearch] Query: '{query}' (engine={engine_name}, "
                    f"max={limit})\n"
                    f"  No HTTP backend available (install httpx or requests)."
                ),
                data={"query": query, "results": [], "engine": engine_name},
            )

        # In a real build, this would call a search API. For now, return
        # a descriptive message so the caller knows the tool is wired.
        return ToolResult.ok(
            text=(
                f"[WebSearch] Query: '{query}' (engine={engine_name}, "
                f"max={limit})\n"
                f"  Backend: {backend}\n"
                f"  Note: Search API integration pending — configure a "
                f"search provider to enable live results."
            ),
            data={"query": query, "results": [], "engine": engine_name, "backend": backend},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. WebFetch
# ═══════════════════════════════════════════════════════════════════════════════


class WebFetchTool(BaseTool):
    """Fetch and return the text content of a URL."""

    name = "web_fetch"
    description = "Fetch the contents of a URL and return it as text"
    parameters = (
        ParamSpec("url", description="The URL to fetch"),
        ParamSpec(
            "prompt",
            description="Optional prompt describing what to extract",
            required=False,
        ),
        ParamSpec(
            "max_bytes",
            type="integer",
            description="Maximum bytes to read (default 500_000)",
            required=False,
        ),
    )

    def __init__(self, default_max_bytes: int = 500_000, timeout: float = 15.0) -> None:
        self._default_max_bytes = default_max_bytes
        self._timeout = timeout

    async def execute(
        self,
        url: str,
        prompt: str | None = None,
        max_bytes: int | None = None,
    ) -> ToolResult:
        limit = max_bytes or self._default_max_bytes

        # Basic URL validation
        if not url.startswith(("http://", "https://")):
            return ToolResult.err(f"Invalid URL (must start with http:// or https://): {url}")

        max_size = min(limit, 2_000_000)

        backend = _resolve_http_backend()
        if backend == "none":
            return ToolResult.ok(
                text=(
                    f"[WebFetch] URL: {url}\n"
                    f"  No HTTP backend available (install httpx or requests)."
                ),
                data={"url": url, "content": "", "backend": None},
            )

        try:
            status, content = _http_get(url, timeout=self._timeout)

            if len(content) > max_size:
                content = content[:max_size] + f"\n... truncated ({len(content) - max_size:,} bytes omitted)"

            summary = f"Fetched {url} ({status}, {len(content):,} bytes)"
            if prompt:
                summary += f"\nExtraction prompt: {prompt}"

            return ToolResult.ok(
                text=f"--- {summary}\n{content}",
                data={"url": url, "status": status, "content": content, "bytes": len(content)},
            )

        except Exception as exc:
            return ToolResult.err(f"Failed to fetch '{url}': {exc}")
