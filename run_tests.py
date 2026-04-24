"""Minimal test runner — imports everything and runs basic checks."""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

errors = []

# 1. Import check
try:
    from tool_registry import (
        BaseTool, ToolResult, ParamSpec, ToolRegistry,
        ReadTool, WriteTool, EditTool, GlobTool, GrepTool,
        WebSearchTool, WebFetchTool,
    )
    print("OK: tool_registry imports")
except Exception as e:
    errors.append(f"import failed: {e}")
    print(f"FAIL: import — {e}")

# 2. ToolResult basics
try:
    r = ToolResult.ok(text="ok")
    assert r.success and r.text == "ok"
    r2 = ToolResult.err("bad")
    assert not r2.success and "Error:" in r2.text
    print("OK: ToolResult.ok/err")
except Exception as e:
    errors.append(f"ToolResult: {e}")
    print(f"FAIL: ToolResult — {e}")

# 3. WriteTool + ReadTool round-trip
import tempfile
from pathlib import Path
import asyncio

async def test_file_tools():
    with tempfile.TemporaryDirectory() as td:
        root = (Path(td) / "sandbox").resolve()
        root.mkdir()

        # Write
        target = root / "test.txt"
        wt = WriteTool(allowed_roots=(str(root),))
        r = await wt.execute(str(target), "hello world")
        assert r.success, f"write failed: {r.text}"

        # Read
        rt = ReadTool(allowed_roots=(str(root),))
        r = await rt.execute(str(target))
        assert r.success and "hello world" in r.text, f"read failed: {r.text}"

        # Edit
        et = EditTool(allowed_roots=(str(root),))
        r = await et.execute(str(target), "world", "there")
        assert r.success, f"edit failed: {r.text}"
        assert target.read_text(encoding="utf-8") == "hello there"

        # Glob
        gt = GlobTool(allowed_roots=(str(root),))
        r = await gt.execute("*.txt", path=str(root))
        assert r.success and "test.txt" in r.text, f"glob failed: {r.text}"

        # Grep
        grt = GrepTool(allowed_roots=(str(root),))
        r = await grt.execute("hello", path=str(root))
        assert r.success, f"grep failed: {r.text}"

        print("OK: file tools round-trip")

async def test_web_tools():
    # WebSearch
    ws = WebSearchTool()
    r = await ws.execute("test query")
    assert r.success, f"web_search failed: {r.text}"

    # WebFetch with invalid URL
    wf = WebFetchTool()
    r = await wf.execute("not-a-url")
    assert not r.success, "invalid URL should fail"

    print("OK: web tools basics")

async def test_registry():
    with tempfile.TemporaryDirectory() as td:
        root = (Path(td) / "sandbox").resolve()
        root.mkdir()

        reg = ToolRegistry()
        reg.register(ReadTool(allowed_roots=(str(root),)))
        reg.register(WriteTool(allowed_roots=(str(root),)))

        assert reg.count == 2
        assert reg.get("read") is not None
        assert reg.list_by_category("read")

        # Execute through registry
        target = root / "reg.txt"
        r = await reg.execute("write", file_path=str(target), content="registry works")
        assert r.success, f"registry write: {r.text}"

        r = await reg.execute("read", file_path=str(target))
        assert r.success and "registry works" in r.text

        # safe_execute
        r_str = await reg.safe_execute("unknown")
        assert "Error" in r_str

        print("OK: ToolRegistry")

async def main():
    await test_file_tools()
    await test_web_tools()
    await test_registry()
    print("\nAll tests passed!")

try:
    asyncio.run(main())
except Exception as e:
    errors.append(str(e))
    print(f"FAIL: {e}")

if errors:
    print(f"\n{len(errors)} error(s):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
sys.exit(0)
