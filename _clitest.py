import sys, os

# Write straight to a file without stdio
f = open(r"D:\claude-code-project\langchain\agentsFactory\_cli_test_out.log", "w")
try:
    f.write(f"python: {sys.executable}\n")
    f.write(f"cwd: {os.getcwd()}\n")

    # Check import
    try:
        from click.testing import CliRunner
        f.write("click OK\n")
    except Exception as e:
        f.write(f"click FAIL: {e}\n")

    try:
        from harnesses.cli import research
        f.write("cli import OK\n")
    except Exception as e:
        f.write(f"cli FAIL: {e}\n")

    # Run tests
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_harness_cli.py", "-v"],
        capture_output=True, text=True,
        cwd=r"D:\claude-code-project\langchain\agentsFactory"
    )
    f.write(f"exit_code: {result.returncode}\n")
    f.write(f"output:\n{result.stdout[-3000:]}\n")
    f.write(f"stderr:\n{result.stderr[-3000:]}\n")
except Exception as e:
    f.write(f"FATAL: {e}\n")
finally:
    f.close()
