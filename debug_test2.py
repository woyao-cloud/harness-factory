"""Debug test with inline tracing."""
import sys
sys.path.insert(0, "D:/claude-code-project/langchain/agentsFactory")
from pathlib import Path
from runtime.types import ToolCall

# Monkey-patch allow_path_prefix to add debug output
import runtime.security as sec
original = sec.allow_path_prefix

def debug_allow_path_prefix(allowed_prefixes):
    policy = original(allowed_prefixes)
    def debug_policy(tc):
        raw_path = tc.params.get("file_path") or tc.params.get("path") or ""
        print(f"DEBUG allow_path_prefix: raw_path={raw_path!r}")
        print(f"DEBUG allowed_prefixes={allowed_prefixes!r}")

        resolved = str(Path(raw_path).resolve()).replace("\\", "/")
        print(f"DEBUG resolved={resolved!r}")

        for p in allowed_prefixes:
            np = p.replace("\\", "/")
            print(f"DEBUG prefix={p!r}, norm={np!r}")
            print(f"DEBUG {resolved!r}.startswith({np!r}) = {resolved.startswith(np)}")

        result = policy(tc)
        print(f"DEBUG result={result}")
        return result
    return debug_policy

sec.allow_path_prefix = debug_allow_path_prefix

from runtime.security import allow_path_prefix, Decision

research_dir = str(Path("./research").resolve())
policy = allow_path_prefix((research_dir,))
tc = ToolCall(tool_name="write", params={"file_path": "research/test.md", "content": "hello"})
result = policy(tc)
print(f"Final result: {result}")
