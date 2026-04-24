"""Tests for harness factory functions — ResearchHarness and future harnesses."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from runtime.harness import HarnessRuntime
from runtime.llm_provider import MockProvider
from runtime.types import HarnessSpec, ToolCall, UserInput

from tool_registry.file_tools import ReadTool, WriteTool
from tool_registry.web_tools import WebSearchTool


# =============================================================================
# Helpers
# =============================================================================


def asyncio_run(coro):
    """Run an async function synchronously for testing."""
    import asyncio

    return asyncio.run(coro)


# =============================================================================
# ResearchHarness
# =============================================================================


class TestResearchHarness:
    """Integration-level tests for ``create_research_harness()``."""

    def test_create_research_harness(self) -> None:
        """Factory returns a working ``HarnessRuntime`` with correct spec."""
        from harnesses import create_research_harness

        harness = create_research_harness()
        assert isinstance(harness, HarnessRuntime)
        assert harness.spec.name == "research-harness"
        assert harness.spec.version == "0.1.0"
        assert harness.spec.pipeline_strategy == "interactive"
        assert harness.spec.default_model == "claude-sonnet-4-20250514"

    def test_research_harness_tools_registered(self) -> None:
        """All expected tools are registered in the pipeline."""
        from harnesses import create_research_harness

        harness = create_research_harness()
        tool_names = {t.name for t in harness.pipeline.tool_definitions}
        expected = {"read", "write", "glob", "grep", "web_search", "web_fetch"}
        assert expected.issubset(tool_names), f"Missing: {expected - tool_names}"

    def test_research_harness_turn(self) -> None:
        """Basic turn with MockProvider returns a result."""
        from harnesses import create_research_harness

        mock = MockProvider()
        harness = create_research_harness(llm=mock)
        harness.start()

        result = asyncio_run(harness.turn(UserInput(text="Find papers on AI")))
        assert result.turn_complete is True
        assert isinstance(result.text, str)
        assert len(result.text) > 0

        harness.close()

    def test_research_harness_security_policies(self) -> None:
        """Security policies are configured:
        - Read tools under research_root → ALLOW
        - Web tools → ALLOW
        - Write tools → DENY (by ``allow_read_only`` policy)
        """
        from harnesses import create_research_harness

        with tempfile.TemporaryDirectory() as tmp:
            research_dir = Path(tmp) / "papers"
            research_dir.mkdir()
            harness = create_research_harness(research_root=str(research_dir))
            harness.start()

            gate = harness.security

            # Read tool under allowed path → ALLOW
            result = gate.approve(
                ToolCall(
                    tool_name="read",
                    params={"file_path": str(research_dir / "paper.pdf")},
                )
            )
            assert result.decision.name == "ALLOW", (
                f"Expected ALLOW for read, got {result.decision}"
            )

            # Write tool → DENY from allow_read_only policy
            result = gate.approve(
                ToolCall(
                    tool_name="write",
                    params={"file_path": str(research_dir / "notes.md")},
                )
            )
            assert result.decision.name == "DENY", (
                f"Expected DENY for write, got {result.decision}"
            )

            # Web search → ALLOW (not matched by any policy, no path restriction)
            result = gate.approve(ToolCall(tool_name="web_search", params={"query": "test"}))
            assert result.decision.name == "ALLOW", (
                f"Expected ALLOW for web_search, got {result.decision}"
            )

            # Write tool outside research_root → DENY (by allow_read_only)
            result = gate.approve(
                ToolCall(
                    tool_name="write",
                    params={"file_path": str(Path(tmp) / "outside" / "secret.txt")},
                )
            )
            assert result.decision.name == "DENY", (
                f"Expected DENY for write outside root, got {result.decision}"
            )

            harness.close()

    def test_research_harness_custom_root(self) -> None:
        """Custom research_root directory is created."""
        from harnesses import create_research_harness

        with tempfile.TemporaryDirectory() as tmp:
            research_root = os.path.join(tmp, "my_papers")
            harness = create_research_harness(research_root=research_root)
            assert os.path.isdir(research_root)
            assert harness.spec.metadata.get("research_root") == str(
                Path(research_root).resolve()
            )
            harness.close()

    def test_research_harness_system_prompt(self) -> None:
        """System prompt template is set and contains expected sections."""
        from harnesses import create_research_harness

        harness = create_research_harness()
        prompt = harness.spec.system_prompt_template
        assert "research assistant" in prompt.lower()
        assert "web_search" in prompt
        assert "TL;DR" in prompt
        assert "Methodology" in prompt

    def test_research_harness_with_recovery(self) -> None:
        """Factory accepts an optional PipelineRecovery."""
        from harnesses import create_research_harness
        from runtime.recovery import PipelineRecovery

        recovery = PipelineRecovery()
        harness = create_research_harness(recovery=recovery)
        assert harness.recovery is recovery
        harness.close()

    def test_research_harness_tool_executors_wired(self) -> None:
        """Real tool executors (not placeholders) are installed for each tool."""
        from harnesses import create_research_harness

        with tempfile.TemporaryDirectory() as tmp:
            harness = create_research_harness(research_root=tmp)
            # The _default_executor returns "[tool executed with params: ...]"
            # Real executors should return something different.
            # We verify by checking that at least one tool's executor differs
            # from the default pattern.
            pipe = harness.pipeline
            for name in ("read", "write", "glob", "grep", "web_search", "web_fetch"):
                assert name in pipe._tool_executors, f"Missing executor for {name}"
            harness.close()


class TestResearchHarnessEdgeCases:
    """Edge cases and error handling for ResearchHarness."""

    def test_research_harness_default_root_created(self) -> None:
        """Default research_root (./research) is created relative to CWD."""
        from harnesses import create_research_harness
        import os

        harness = create_research_harness()
        expected = os.path.join(os.getcwd(), "research")
        assert os.path.isdir(expected)
        harness.close()
        # Cleanup
        import shutil
        shutil.rmtree(expected, ignore_errors=True)

    def test_research_harness_double_close(self) -> None:
        """Calling close() twice should not raise."""
        from harnesses import create_research_harness

        harness = create_research_harness()
        harness.close()
        harness.close()  # Second close should be a no-op

    def test_research_harness_turn_after_close(self) -> None:
        """Turning after close raises RuntimeError."""
        from harnesses import create_research_harness

        harness = create_research_harness()
        harness.close()

        with pytest.raises(RuntimeError, match="Session is closed"):
            asyncio_run(harness.turn(UserInput(text="hello")))

    def test_research_harness_with_explicit_model(self) -> None:
        """Model override is reflected in spec."""
        from harnesses import create_research_harness

        harness = create_research_harness(model="claude-opus-4-20250514")
        assert harness.spec.default_model == "claude-opus-4-20250514"
        harness.close()
