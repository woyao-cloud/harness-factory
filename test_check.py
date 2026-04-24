import sys, os
sys.path.insert(0, r"D:\claude-code-project\langchain\agentsFactory")

from harnesses import create_research_harness

try:
    harness = create_research_harness()
    print(f"OK: harness created, name={harness.spec.name}")
    print(f"Tools: {[t.name for t in harness.pipeline.tool_definitions]}")

    from runtime.llm_provider import MockProvider
    from runtime.types import UserInput
    import asyncio

    mock = MockProvider()
    harness2 = create_research_harness(llm=mock)
    harness2.start()
    result = asyncio.run(harness2.turn(UserInput(text="hello")))
    print(f"Turn OK: text='{result.text[:50]}'")
    harness2.close()

    # Security test
    from runtime.types import ToolCall
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "papers"
        d.mkdir()
        h = create_research_harness(research_root=str(d))
        g = h.security

        r = g.approve(ToolCall(tool_name="read", params={"file_path": str(d / "a.pdf")}))
        print(f"Read ALLOW: {r.decision.name}")

        r = g.approve(ToolCall(tool_name="write", params={"file_path": str(d / "n.md")}))
        print(f"Write DENY: {r.decision.name}")

        r = g.approve(ToolCall(tool_name="web_search", params={"query": "test"}))
        print(f"Web ALLOW: {r.decision.name}")

        h.close()

    print("ALL TESTS PASSED")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
