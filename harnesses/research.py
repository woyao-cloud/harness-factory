"""ResearchHarness factory — paper search + fetch + read + summarize.

Factory function that creates a fully wired ``HarnessRuntime`` with:

* Web search & fetch tools for finding papers
* File tools for reading/writing papers & notes
* Research-oriented system prompt
* Sensible security defaults (read-allowed, write-ask)

Usage::

    from harnesses import create_research_harness

    harness = create_research_harness(research_root="./papers")
    harness.start()
    result = await harness.turn(UserInput(text="Find papers on topic X"))
    harness.close()
"""

from __future__ import annotations

from pathlib import Path

from runtime.harness import HarnessConfig, HarnessRuntime
from runtime.recovery import PipelineRecovery
from runtime.types import HarnessSpec
from tool_registry import ToolRegistry
from tool_registry.file_tools import GlobTool, GrepTool, ReadTool, WriteTool
from tool_registry.web_tools import WebFetchTool, WebSearchTool

from .base import ensure_dir, read_only_path_policy

# ---------------------------------------------------------------------------
# Research assistant system prompt
# ---------------------------------------------------------------------------

RESEARCH_PROMPT = """\
You are a research assistant with access to web search and file tools.

## Your capabilities

1. **Search for papers** — Use ``web_search`` to find academic papers, articles,
   and resources on any research topic.
2. **Fetch content** — Use ``web_fetch`` to retrieve paper abstracts,
   full text, or supplementary materials from URLs.
3. **Read local papers** — Use ``read`` to examine papers already saved
   in the research workspace.
4. **Search within papers** — Use ``grep`` to find specific terms,
   methodologies, or citations across your paper collection.
5. **Save notes & summaries** — Use ``write`` to save your analyses,
   summaries, and research notes.

## Guidelines

* When asked to research a topic, first search for relevant papers.
* Provide structured summaries: TL;DR, methodology, key findings, limitations.
* Track citations and note connections between papers.
* Suggest related research directions and open questions.
* Save important findings as notes in the research directory.
* Always cite your sources with URLs when available.
* If you cannot find sufficient information, be transparent about limitations.

## Output style

Use clear Markdown formatting. For paper summaries, use this structure:

```markdown
## Paper Title (Year)

**TL;DR:** One-sentence summary.

**Methodology:** Key approach and techniques.

**Key Findings:** Bullet-point list of main results.

**Limitations:** Methodological concerns or scope constraints.

**Links:** [URLs or file paths]
```
"""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_research_harness(
    research_root: str = "./research",
    llm=None,
    model: str = "deepseek-v4-flash:cloud",
    recovery: PipelineRecovery | None = None,
    checkpoint_store=None,
    max_tool_rounds: int = 25,
    enable_auto_save: bool = False,
) -> HarnessRuntime:
    """Create a fully wired ResearchHarness.

    Args:
        research_root: Directory for paper files and notes.
        llm: LLM provider (defaults to MockProvider).
        model: Model name string.
        recovery: Optional PipelineRecovery for error resilience.
        checkpoint_store: Optional CheckpointStore for session persistence.
        max_tool_rounds: Maximum tool calls per turn.
        enable_auto_save: Enable automatic checkpoint save.

    Returns:
        A configured ``HarnessRuntime`` ready for ``start()`` / ``turn()``.
    """
    registry = ToolRegistry()
    root = ensure_dir(research_root)
    allowed = (str(root),)

    # ── Register tools ────────────────────────────────────────────────
    registry.register(ReadTool(allowed_roots=allowed))
    registry.register(WriteTool(allowed_roots=allowed))
    registry.register(GlobTool(allowed_roots=allowed))
    registry.register(GrepTool(allowed_roots=allowed))
    registry.register(WebSearchTool())
    registry.register(WebFetchTool())

    # ── Build spec ───────────────────────────────────────────────────
    spec = HarnessSpec(
        name="research-harness",
        version="0.1.0",
        description="Research assistant: search, fetch, read, and "
        "summarize academic papers",
        tools=tuple(registry.definitions()),
        system_prompt_template=RESEARCH_PROMPT,
        pipeline_strategy="interactive",
        default_model=model,
        metadata={"research_root": str(root)},
    )

    # ── Harness config ───────────────────────────────────────────────
    config = HarnessConfig(
        max_tool_rounds=max_tool_rounds,
        security_default_decision="ask",
        security_require_approval=("write", "edit"),
        pipeline_strategy="interactive",
        enable_auto_save=enable_auto_save,
    )

    # ── Build harness ────────────────────────────────────────────────
    harness = HarnessRuntime(
        spec=spec,
        llm=llm,
        config=config,
        recovery=recovery,
        checkpoint_store=checkpoint_store,
    )

    # Install real tool executors (overrides default placeholder)
    registry.install_all(harness)

    # Security: allow reads under research_root, ask for mutations
    for policy in read_only_path_policy(allowed):
        harness.add_security_policy(policy)

    return harness
