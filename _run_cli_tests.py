"""Run CLI tests and write results to a file."""
import subprocess, sys, os

os.chdir(r"D:\claude-code-project\langchain\agentsFactory")
log = open("_cli_test_output.txt", "w", encoding="utf-8")

log.write(f"Python: {sys.executable}\n")
log.write(f"CWD: {os.getcwd()}\n\n")

# First try importing
sys.path.insert(0, r"D:\claude-code-project\langchain\agentsFactory")
try:
    from click.testing import CliRunner
    log.write("CliRunner imported OK\n")
except Exception as e:
    log.write(f"CliRunner import error: {e}\n")

try:
    from harnesses.cli import research
    log.write("harnesses.cli imported OK\n")
except Exception as e:
    log.write(f"harnesses.cli import error: {e}\n")

log.write("\n--- Running pytest ---\n")
result = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/test_harness_cli.py", "-v", "--tb=long"],
    capture_output=True, text=True,
    cwd=r"D:\claude-code-project\langchain\agentsFactory"
)
log.write(f"Exit code: {result.returncode}\n")
log.write("\n=== STDOUT ===\n")
log.write(result.stdout)
log.write("\n=== STDERR ===\n")
log.write(result.stderr)
log.close()
