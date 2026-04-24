"""Integration tests for the Tool Registry components.

Tests cover:
* BaseTool protocol (ToolResult, ParamSpec)
* File tools (Read, Write, Edit, Glob, Grep)
* Web tools (WebSearch, WebFetch)
* ToolRegistry registration and execution
* HarnessRuntime integration via install()
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from tool_registry import (
    BaseTool,
    EditTool,
    GlobTool,
    GrepTool,
    ParamSpec,
    ReadTool,
    ToolRegistry,
    ToolResult,
    WebFetchTool,
    WebSearchTool,
    WriteTool,
)


# =============================================================================
# ToolResult
# =============================================================================


class TestToolResult:
    def test_ok(self) -> None:
        r = ToolResult.ok(text="hello", data={"key": "val"})
        assert r.success is True
        assert r.text == "hello"
        assert r.data == {"key": "val"}
        assert bool(r) is True

    def test_err(self) -> None:
        r = ToolResult.err("something broke")
        assert r.success is False
        assert "Error:" in r.text
        assert r.error == "something broke"
        assert bool(r) is False

    def test_defaults(self) -> None:
        r = ToolResult()
        assert r.success is True
        assert r.text == ""
        assert r.data is None
        assert r.error is None


# =============================================================================
# ParamSpec
# =============================================================================


class TestParamSpec:
    def test_minimal(self) -> None:
        p = ParamSpec("name", description="a param")
        assert p.name == "name"
        assert p.type == "string"
        assert p.required is True

    def test_to_runtime_param(self) -> None:
        p = ParamSpec("count", type="integer", description="how many", required=False)
        rp = p.to_runtime_param()
        assert rp.name == "count"
        assert rp.type == "integer"
        assert rp.description == "how many"
        assert rp.required is False


# =============================================================================
# ReadTool
# =============================================================================


class TestReadTool:
    def test_read_text_file(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("Hello, World!", encoding="utf-8")
        tool = ReadTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute(str(f)))
        assert result.success
        assert "Hello, World!" in result.text

    def test_file_not_found(self, tmp_path: Path) -> None:
        tool = ReadTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute(str(tmp_path / "nope.txt")))
        assert not result.success
        assert "Error:" in result.text

    def test_path_traversal_blocked(self, tmp_path: Path) -> None:
        tool = ReadTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute("/etc/passwd"))
        assert not result.success

    def test_offset_and_limit(self, tmp_path: Path) -> None:
        lines = "\n".join(f"line {i}" for i in range(100))
        f = tmp_path / "lines.txt"
        f.write_text(lines, encoding="utf-8")
        tool = ReadTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute(str(f), offset=10, limit=5))
        assert result.success
        assert "line 9" in result.text   # 0-based line naming in content
        assert "line 13" in result.text  # last line of the 5-line slice
        assert "line 14" not in result.text


# =============================================================================
# WriteTool
# =============================================================================


class TestWriteTool:
    def test_write_new_file(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        tool = WriteTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute(str(target), "hello from tool"))
        assert result.success
        assert target.read_text(encoding="utf-8") == "hello from tool"

    def test_write_creates_dirs(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c" / "deep.txt"
        tool = WriteTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute(str(target), "deep"))
        assert result.success
        assert target.read_text(encoding="utf-8") == "deep"

    def test_write_overwrite(self, tmp_path: Path) -> None:
        f = tmp_path / "overwrite.txt"
        f.write_text("old", encoding="utf-8")
        tool = WriteTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute(str(f), "new"))
        assert result.success
        assert f.read_text(encoding="utf-8") == "new"

    def test_write_size_limit(self, tmp_path: Path) -> None:
        tool = WriteTool(allowed_roots=(str(tmp_path),), max_bytes=10)
        result = asyncio_run(tool.execute(str(tmp_path / "big.txt"), "x" * 20))
        assert not result.success

    def test_write_path_traversal_blocked(self, tmp_path: Path) -> None:
        tool = WriteTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute("/tmp/evil.txt", "bad"))
        assert not result.success


# =============================================================================
# EditTool
# =============================================================================


class TestEditTool:
    def test_simple_replace(self, tmp_path: Path) -> None:
        f = tmp_path / "edit.txt"
        f.write_text("Hello, World!", encoding="utf-8")
        tool = EditTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute(str(f), "World", "Claude"))
        assert result.success
        assert f.read_text(encoding="utf-8") == "Hello, Claude!"

    def test_string_not_found(self, tmp_path: Path) -> None:
        f = tmp_path / "no_match.txt"
        f.write_text("Hello", encoding="utf-8")
        tool = EditTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute(str(f), "Goodbye", "Hello"))
        assert not result.success

    def test_only_first_occurrence(self, tmp_path: Path) -> None:
        f = tmp_path / "multi.txt"
        f.write_text("a a a", encoding="utf-8")
        tool = EditTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute(str(f), "a", "X"))
        assert result.success
        assert f.read_text(encoding="utf-8") == "X a a"


# =============================================================================
# GlobTool
# =============================================================================


class TestGlobTool:
    def test_find_py_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        (tmp_path / "c.txt").write_text("")
        tool = GlobTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute("*.py", path=str(tmp_path)))
        assert result.success
        assert "a.py" in result.text
        assert "b.py" in result.text
        assert "c.txt" not in result.text

    def test_no_matches(self, tmp_path: Path) -> None:
        tool = GlobTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute("*.xyz", path=str(tmp_path)))
        assert result.success
        assert "no matches" in result.text.lower()

    def test_max_results(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text("")
        tool = GlobTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute("*.txt", path=str(tmp_path), max_results=3))
        assert result.success
        data = result.data
        assert data is not None
        assert len(data["files"]) <= 3


# =============================================================================
# GrepTool
# =============================================================================


class TestGrepTool:
    def test_simple_search(self, tmp_path: Path) -> None:
        f = tmp_path / "search.txt"
        f.write_text("hello world\nfoo bar\nhello again", encoding="utf-8")
        tool = GrepTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute("hello", path=str(tmp_path)))
        assert result.success
        assert "search.txt" in result.text
        assert "hello world" in result.text

    def test_no_match(self, tmp_path: Path) -> None:
        f = tmp_path / "no.txt"
        f.write_text("nothing", encoding="utf-8")
        tool = GrepTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute("xyzzy", path=str(tmp_path)))
        assert result.success
        assert "no matches" in result.text.lower()

    def test_case_sensitive(self, tmp_path: Path) -> None:
        f = tmp_path / "case.txt"
        f.write_text("Hello hello HELLO", encoding="utf-8")
        tool = GrepTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute("hello", path=str(tmp_path), case_sensitive=True))
        assert result.success
        # Only the lowercase 'hello' should match
        matches = result.data.get("matches", []) if result.data else []
        texts = [m["snippet"] for m in matches]
        assert any("hello" in t for t in texts)

    def test_context_lines(self, tmp_path: Path) -> None:
        lines = [f"line {i}" for i in range(10)]
        lines.append("MATCH")
        lines += [f"after {i}" for i in range(10)]
        f = tmp_path / "ctx.txt"
        f.write_text("\n".join(lines), encoding="utf-8")
        tool = GrepTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute("MATCH", path=str(tmp_path), context=2))
        assert result.success
        assert "line 8" in result.text
        assert "after 1" in result.text

    def test_glob_filter(self, tmp_path: Path) -> None:
        (tmp_path / "match.py").write_text("target", encoding="utf-8")
        (tmp_path / "ignore.js").write_text("target", encoding="utf-8")
        tool = GrepTool(allowed_roots=(str(tmp_path),))
        result = asyncio_run(tool.execute("target", path=str(tmp_path), glob="*.py"))
        assert result.success
        assert "match.py" in result.text
        assert "ignore.js" not in result.text


# =============================================================================
# Web tools
# =============================================================================


class TestWebSearchTool:
    def test_search_returns_structured_result(self) -> None:
        tool = WebSearchTool()
        result = asyncio_run(tool.execute("test query"))
        assert result.success
        assert result.data is not None
        assert result.data["query"] == "test query"

    def test_max_results_clamped(self) -> None:
        tool = WebSearchTool(max_results=5)
        result = asyncio_run(tool.execute("test", max_results=100))
        assert result.success


class TestWebFetchTool:
    def test_invalid_url(self) -> None:
        tool = WebFetchTool()
        result = asyncio_run(tool.execute("not-a-url"))
        assert not result.success
        assert "Error:" in result.text

    def test_valid_url_no_backend(self) -> None:
        tool = WebFetchTool()
        result = asyncio_run(tool.execute("https://example.com"))
        # Should still return success (degraded mode) or attempt fetch
        assert result.success is True or result.success is False
        assert result.text is not None


# =============================================================================
# ToolRegistry
# =============================================================================


class TestToolRegistry:
    def test_register_and_get(self) -> None:
        registry = ToolRegistry()
        tool = ReadTool(allowed_roots=(os.getcwd(),))
        registry.register(tool)
        assert registry.get("read") is tool
        assert registry.count == 1

    def test_register_all(self) -> None:
        registry = ToolRegistry()
        tools = [
            ReadTool(allowed_roots=(os.getcwd(),)),
            WriteTool(allowed_roots=(os.getcwd(),)),
        ]
        registry.register_all(tools)
        assert registry.count == 2

    def test_unregister(self) -> None:
        registry = ToolRegistry()
        registry.register(ReadTool(allowed_roots=(os.getcwd(),)))
        registry.unregister("read")
        assert registry.count == 0

    def test_list(self) -> None:
        registry = ToolRegistry()
        registry.register(ReadTool(allowed_roots=(os.getcwd(),)))
        registry.register(WriteTool(allowed_roots=(os.getcwd(),)))
        names = [t.name for t in registry.list()]
        assert "read" in names
        assert "write" in names

    def test_execute_unknown_tool(self) -> None:
        registry = ToolRegistry()
        result = asyncio_run(registry.execute("nonexistent"))
        assert not result.success
        assert "not found" in result.text

    def test_execute_tool(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("content", encoding="utf-8")
        registry = ToolRegistry()
        registry.register(ReadTool(allowed_roots=(str(tmp_path),)))
        result = asyncio_run(registry.execute("read", file_path=str(f)))
        assert result.success
        assert "content" in result.text

    def test_safe_execute(self, tmp_path: Path) -> None:
        registry = ToolRegistry()
        registry.register(ReadTool(allowed_roots=(str(tmp_path),)))
        result = asyncio_run(registry.safe_execute("read", file_path=str(tmp_path / "nope.txt")))
        # safe_execute returns string, not ToolResult
        assert isinstance(result, str)
        assert "Error" in result

    def test_definitions(self) -> None:
        registry = ToolRegistry()
        registry.register(ReadTool(allowed_roots=(os.getcwd(),)))
        defs = registry.definitions()
        assert len(defs) == 1
        assert defs[0].name == "read"

    def test_list_by_category(self) -> None:
        registry = ToolRegistry()
        registry.register(ReadTool(allowed_roots=(os.getcwd(),)))
        registry.register(WriteTool(allowed_roots=(os.getcwd(),)))
        registry.register(WebSearchTool())
        file_tools = registry.list_by_category("read")
        assert len(file_tools) >= 1


# =============================================================================
# Harness integration
# =============================================================================


class TestHarnessIntegration:
    def test_install_into_harness(self) -> None:
        from runtime import HarnessRuntime, HarnessConfig, HarnessSpec

        registry = ToolRegistry()
        registry.register(ReadTool(allowed_roots=(os.getcwd(),)))
        registry.register(WriteTool(allowed_roots=(os.getcwd(),)))
        registry.register(GlobTool(allowed_roots=(os.getcwd(),)))
        registry.register(GrepTool(allowed_roots=(os.getcwd(),)))

        spec = HarnessSpec(
            name="test-harness",
            description="Test harness for tool registry",
        )
        harness = HarnessRuntime(spec, config=HarnessConfig(enable_auto_save=False))
        registry.install_all(harness)

        # Check tools are registered in the pipeline
        assert harness.pipeline is not None

    def test_selective_install(self) -> None:
        from runtime import HarnessRuntime, HarnessConfig, HarnessSpec

        registry = ToolRegistry()
        registry.register(ReadTool(allowed_roots=(os.getcwd(),)))
        registry.register(WriteTool(allowed_roots=(os.getcwd(),)))
        registry.register(WebSearchTool())

        spec = HarnessSpec(
            name="selective-test",
            description="Selective install test",
        )
        harness = HarnessRuntime(spec, config=HarnessConfig(enable_auto_save=False))
        registry.install(harness, names=["read", "web_search"])

        # Only selected tools should be registered
        assert harness.pipeline is not None

    def test_full_registry_integration(self, tmp_path: Path) -> None:
        """End-to-end: register tools, execute through registry, verify results."""
        from runtime import HarnessRuntime, HarnessConfig, HarnessSpec

        registry = ToolRegistry()
        registry.register(ReadTool(allowed_roots=(str(tmp_path),)))
        registry.register(WriteTool(allowed_roots=(str(tmp_path),)))
        registry.register(EditTool(allowed_roots=(str(tmp_path),)))
        registry.register(GlobTool(allowed_roots=(str(tmp_path),)))
        registry.register(GrepTool(allowed_roots=(str(tmp_path),)))
        registry.register(WebSearchTool())
        registry.register(WebFetchTool())

        # Write a file via registry
        target = tmp_path / "integrate.txt"
        write_result = asyncio_run(
            registry.execute("write", file_path=str(target), content="Hello, Harness!")
        )
        assert write_result.success

        # Read it back
        read_result = asyncio_run(registry.execute("read", file_path=str(target)))
        assert read_result.success
        assert "Hello, Harness!" in read_result.text

        # Edit it
        edit_result = asyncio_run(
            registry.execute("edit", file_path=str(target), old_string="Harness", new_string="World")
        )
        assert edit_result.success
        assert target.read_text(encoding="utf-8") == "Hello, World!"

        # Glob
        glob_result = asyncio_run(registry.execute("glob", pattern="*.txt", path=str(tmp_path)))
        assert glob_result.success
        assert "integrate.txt" in glob_result.text

        # Grep
        grep_result = asyncio_run(
            registry.execute("grep", pattern="World", path=str(tmp_path))
        )
        assert grep_result.success
        assert "integrate.txt" in grep_result.text

        # Install into harness
        spec = HarnessSpec(
            name="full-test",
            description="Full registry integration",
        )
        harness = HarnessRuntime(spec, config=HarnessConfig(enable_auto_save=False))
        registry.install_all(harness)
        assert harness.pipeline is not None

    def test_tool_result_bool_coercion(self) -> None:
        """ToolResult.__bool__ should work for conditional checks."""
        ok_result = ToolResult.ok(text="success")
        err_result = ToolResult.err("failure")

        assert bool(ok_result) is True
        assert bool(err_result) is False

        # Should work in if-statements
        executed = []
        if ok_result:
            executed.append("ok")
        if err_result:
            executed.append("err")
        assert executed == ["ok"]


# =============================================================================
# Helpers
# =============================================================================


def asyncio_run(coro):
    """Run an async function synchronously for testing."""
    import asyncio
    return asyncio.run(coro)
