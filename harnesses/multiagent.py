"""Multi-agent research harness factory.

Creates a fully wired AgentCoordinator with Planner, Worker, and Review agents,
each configured with the research tool set and appropriate system prompts.

Usage::

    from harnesses.multiagent import create_multiagent_coordinator

    coordinator = create_multiagent_coordinator(
        research_root="./research",
        llm=llm,
    )
    result = await coordinator.run("Research topic X")
"""

from __future__ import annotations

from pathlib import Path

from runtime.agents import (
    AgentCoordinator,
    CoordinatorConfig,
    PlannerAgent,
    ReviewAgent,
    WorkerAgent,
)
from runtime.llm_provider import LLMProvider, MockProvider
from tool_registry import (
    EditTool,
    GlobTool,
    GrepTool,
    ReadTool,
    ToolRegistry,
    WebFetchTool,
    WebSearchTool,
    WriteTool,
)
from tool_registry.registry import _AliasTool


def create_multiagent_coordinator(
    research_root: str = "./research",
    llm: LLMProvider | None = None,
    model: str = "deepseek-v4-flash:cloud",
    max_iterations: int = 3,
    verbose: bool = False,
) -> AgentCoordinator:
    """Create a fully wired multi-agent research coordinator.

    Args:
        research_root: Directory for research files.
        llm: LLM provider (defaults to MockProvider).
        model: Model name string.
        max_iterations: Maximum review cycles.
        verbose: Enable detailed logging.

    Returns:
        A configured AgentCoordinator ready for ``run()``.
    """
    llm = llm or MockProvider()
    root = Path(research_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    allowed = (str(root),)

    # ── Create tool registry with all research tools ─────────────────
    registry = ToolRegistry()

    # File tools (worker only)
    file_tools = [
        ReadTool(allowed_roots=allowed),
        WriteTool(allowed_roots=allowed),
        EditTool(allowed_roots=allowed),
        GlobTool(allowed_roots=allowed),
        GrepTool(allowed_roots=allowed),
    ]

    # Web tools (planner + worker)
    web_search = WebSearchTool()
    web_fetch = WebFetchTool()
    search_alias = _AliasTool(web_search, "search")  # LLMs often call it just "search"
    web_tools = [web_search, web_fetch, search_alias]

    # Register all tools (for reference)
    for tool in file_tools + web_tools:
        registry.register(tool)

    # ── Create agents ───────────────────────────────────────────────
    # Planner: web tools only (research before planning)
    planner = PlannerAgent(llm=llm, tools=web_tools, model=model)

    # Worker: all tools (file + web)
    worker = WorkerAgent(llm=llm, tools=file_tools + web_tools, model=model)

    # Reviewer: no tools (text-based review only — some models 500 on tool defs)
    reviewer = ReviewAgent(
        llm=llm,
        tools=None,
        model=model,
    )

    # ── Create coordinator ──────────────────────────────────────────
    config = CoordinatorConfig(
        max_iterations=max_iterations,
        verbose=verbose,
        workspace_dir=research_root,
    )
    return AgentCoordinator(
        planner=planner,
        worker=worker,
        reviewer=reviewer,
        config=config,
    )
