"""Shared fixtures and configuration for tool_registry tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    """Create a directory with sample files for testing."""
    d = tmp_path / "sample"
    d.mkdir()

    (d / "hello.py").write_text("print('hello')", encoding="utf-8")
    (d / "hello.txt").write_text("Hello, World!", encoding="utf-8")
    (d / "sub").mkdir()
    (d / "sub" / "deep.txt").write_text("deep content", encoding="utf-8")
    return d
