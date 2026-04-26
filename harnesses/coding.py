"""Multi-agent coding harness factory.

Creates a fully wired AgentCoordinator with Planner, Worker, and Review agents,
each configured with coding-specific prompts and file tools for code generation.

Usage::

    coordinator = create_coding_coordinator(
        output_dir="./coding",
        llm=llm,
    )
    result = await coordinator.run("Build a Python calculator")
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
    WriteTool,
)

# ── Coding-specific prompts ────────────────────────────────────────────────

CODING_PLANNER_SYSTEM_PROMPT = """\
You are a planning agent for software development. Break the coding request into 3-8 clear subtasks.

Guidelines:
- Each subtask should create one file or logical component
- Set depends_on for files or components that depend on others
- Include a testing subtask at the end
- Be specific about file names and paths
- Output ONLY the plan — no conversation, no extra text"""

CODING_WORKER_SYSTEM_PROMPT = """\
You are a coding agent. Write clean, well-structured code with tests.

Available tools: read, write, edit, glob, grep

Write each file in order, respecting dependencies. Use the write and edit tools
to create and modify source code files. Create unit tests for all implemented functionality.
When ALL tasks are done, output a ## Work Report section with each task's status."""

CODING_WORKER_INSTRUCTIONS = (
    "Work through each task in order. Use the available tools to "
    "write source code files and create unit tests. "
    "Use write to create new files, edit to modify existing files."
)

CODING_REVIEWER_SYSTEM_PROMPT = """\
You are a code review agent. Verify the written code against the plan.

Criteria: all files are created, code is syntactically correct,
tests are comprehensive, follows best practices.

Review the work result text below. Output ## Review with \
**Verdict**: (PASS/FAIL/NEEDS_REVISION), **Feedback**:, and per-task results."""


def create_coding_coordinator(
    output_dir: str = "./coding",
    llm: LLMProvider | None = None,
    model: str = "deepseek-v4-flash:cloud",
    max_iterations: int = 3,
    verbose: bool = False,
) -> AgentCoordinator:
    """Create a fully wired multi-agent coding coordinator.

    Args:
        output_dir: Directory for generated code files.
        llm: LLM provider (defaults to MockProvider).
        model: Model name string.
        max_iterations: Maximum review cycles.
        verbose: Enable detailed logging.

    Returns:
        A configured AgentCoordinator ready for ``run()``.
    """
    llm = llm or MockProvider()
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    allowed = (str(root),)

    # ── Create tool registry with file tools only ──────────────────
    registry = ToolRegistry()

    file_tools = [
        ReadTool(allowed_roots=allowed),
        WriteTool(allowed_roots=allowed),
        EditTool(allowed_roots=allowed),
        GlobTool(allowed_roots=allowed),
        GrepTool(allowed_roots=allowed),
    ]

    for tool in file_tools:
        registry.register(tool)

    # ── Create agents ──────────────────────────────────────────────
    # Planner: file tools (to read existing code structure if needed)
    planner = PlannerAgent(
        llm=llm,
        tools=file_tools,
        model=model,
        system_prompt=CODING_PLANNER_SYSTEM_PROMPT,
    )

    # Worker: file tools only for writing code and tests
    worker = WorkerAgent(
        llm=llm,
        tools=file_tools,
        model=model,
        system_prompt=CODING_WORKER_SYSTEM_PROMPT,
        instructions_override=CODING_WORKER_INSTRUCTIONS,
    )

    # Reviewer: no tools (text-based review only)
    reviewer = ReviewAgent(
        llm=llm,
        tools=None,
        model=model,
        system_prompt=CODING_REVIEWER_SYSTEM_PROMPT,
    )

    # ── Create coordinator ─────────────────────────────────────────
    config = CoordinatorConfig(
        max_iterations=max_iterations,
        verbose=verbose,
    )
    return AgentCoordinator(
        planner=planner,
        worker=worker,
        reviewer=reviewer,
        config=config,
    )
