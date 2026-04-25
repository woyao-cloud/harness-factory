"""Run pytest and write output to a file."""
import sys, subprocess

result = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/test_harness_cli.py", "-v", "--tb=long"],
    capture_output=True, text=True,
    cwd=r"D:\claude-code-project\langchain\agentsFactory"
)
sys.stdout.write(result.stdout)
sys.stderr.write(result.stderr)
sys.exit(result.returncode)
