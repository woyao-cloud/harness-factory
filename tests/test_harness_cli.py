"""Tests for harnesses/cli.py — CLI entry point."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from harnesses.cli import research


def test_cli_help() -> None:
    """research --help shows usage."""
    runner = CliRunner()
    result = runner.invoke(research, ["--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "--provider" in result.output
    assert "--repl" in result.output
    assert "--research-root" in result.output
    assert "--mock" in result.output


def test_cli_single_shot_no_query() -> None:
    """Single-shot mode without query prints error."""
    runner = CliRunner()
    result = runner.invoke(research, ["--mock"])
    assert result.exit_code == 1
    assert "QUERY required" in result.output


def test_cli_single_shot_with_query() -> None:
    """Single-shot mode with query runs successfully with mock LLM."""
    runner = CliRunner()
    result = runner.invoke(research, [
        "--mock",
        "Find papers on AI",
        "--research-root", str(Path.cwd() / "research"),
    ])
    assert result.exit_code == 0
    assert "[mock] received:" in result.output


def test_cli_repl_flag() -> None:
    """--repl flag enters interactive mode."""
    runner = CliRunner()
    result = runner.invoke(research, ["--repl", "--mock", "--research-root", "."], input="hello\nexit\n")
    assert result.exit_code == 0
    assert "Research REPL" in result.output
    assert "[mock] received:" in result.output


def test_cli_bad_provider() -> None:
    """Unknown --provider shows error."""
    runner = CliRunner()
    result = runner.invoke(research, [
        "query",
        "--provider", "nonexistent",
    ])
    assert result.exit_code == 1
    assert "Unknown provider" in result.output


def test_cli_missing_api_key() -> None:
    """Missing API key for provider shows error."""
    old = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        runner = CliRunner()
        result = runner.invoke(research, [
            "query",
            "--provider", "anthropic",
        ])
        assert result.exit_code == 1
        assert "API key" in result.output
    finally:
        if old is not None:
            os.environ["ANTHROPIC_API_KEY"] = old


def test_cli_custom_research_root() -> None:
    """--research-root creates the directory."""
    import tempfile
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        research_dir = Path(tmp) / "custom_papers"
        result = runner.invoke(research, [
            "--mock",
            "query",
            "--research-root", str(research_dir),
        ])
        assert result.exit_code == 0
        assert research_dir.is_dir()


def test_cli_repl_empty_input_skipped() -> None:
    """REPL mode skips empty input lines."""
    runner = CliRunner()
    result = runner.invoke(research, ["--repl", "--mock"], input="\n\nexit\n")
    assert result.exit_code == 0
    assert "Research REPL" in result.output


def test_cli_repl_eof_exits() -> None:
    """REPL mode exits gracefully on EOF (Ctrl+D)."""
    runner = CliRunner()
    result = runner.invoke(research, ["--repl", "--mock"], input="exit\n")
    assert result.exit_code == 0
    assert "Research REPL" in result.output


def test_cli_module_execution() -> None:
    """python -m harnesses research --help works."""
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-m", "harnesses", "research", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "Usage:" in result.stdout
