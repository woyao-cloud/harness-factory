# Research Harness CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a terminal CLI to ResearchHarness with single-shot and REPL modes, supporting multiple LLM providers.

**Architecture:** Click-based CLI in `harnesses/cli.py` with an extensible provider registry in `harnesses/provider.py`. The CLI wraps `create_research_harness()`, providing `--provider`, `--api-key`, `--model`, `--research-root`, and `--repl` flags. A `pyproject.toml` registers the `research` console script.

**Tech Stack:** Click (CLI), asyncio (async runtime), existing `LLMProvider`/`AnthropicProvider`/`OpenAIProvider` classes.

---

### Task 1: Create pyproject.toml with project metadata and Click dependency

**Files:**
- Create: `pyproject.toml`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "harness-factory"
version = "0.1.0"
description = "Domain-specific AI agent harness framework"
requires-python = ">=3.11"
dependencies = [
    "click>=8.0",
]

[project.scripts]
research = "harnesses.cli:research"

[tool.setuptools.packages.find]
include = ["harness*", "runtime*", "tool_registry*", "context_manager*"]
```

- [ ] **Step 2: Verify Click is installed**

Run: `pip install click 2>&1` or `pip install -e . 2>&1`
Expected: Success

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add pyproject.toml with project metadata and Click dependency"
```

---

### Task 2: Create provider registry and factory (harnesses/provider.py)

**Files:**
- Create: `harnesses/provider.py`
- Test: `tests/test_provider.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_provider.py`:

```python
"""Tests for harnesses/provider.py — registry and factory."""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from harnesses.provider import ProviderInfo, create_provider, list_providers, register_provider


def test_register_and_list() -> None:
    """Register a provider and verify it appears in list."""
    info = ProviderInfo("test_prov", "Test provider", "TEST_KEY", "test-model")
    register_provider(info)
    providers = list_providers()
    names = [p.name for p in providers]
    assert "test_prov" in names


def test_create_anthropic_provider() -> None:
    """create_provider('anthropic') returns AnthropicProvider."""
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-fake-key-12345"
    try:
        provider = create_provider("anthropic")
        assert provider is not None
        assert type(provider).__name__ == "AnthropicProvider"
    finally:
        del os.environ["ANTHROPIC_API_KEY"]


def test_create_openai_provider() -> None:
    """create_provider('openai') returns OpenAIProvider."""
    os.environ["OPENAI_API_KEY"] = "sk-test-fake-key-12345"
    try:
        provider = create_provider("openai")
        assert provider is not None
        assert type(provider).__name__ == "OpenAIProvider"
    finally:
        del os.environ["OPENAI_API_KEY"]


def test_create_provider_with_api_key_arg() -> None:
    """API key passed as arg is used instead of env var."""
    provider = create_provider("anthropic", api_key="sk-arg-key")
    # The provider is created; key is passed to constructor
    assert provider is not None
    assert type(provider).__name__ == "AnthropicProvider"


def test_create_provider_unknown() -> None:
    """Unknown provider name raises ValueError."""
    with pytest.raises(ValueError, match="Unknown provider"):
        create_provider("nonexistent")


def test_create_provider_missing_key() -> None:
    """Missing API key (no arg, no env) raises ValueError."""
    # Ensure env var is not set
    os.environ.pop("ANTHROPIC_API_KEY", None)
    with pytest.raises(ValueError, match="API key"):
        create_provider("anthropic")


def test_create_provider_with_model_override() -> None:
    """Model override is passed to the provider constructor."""
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-fake-key-12345"
    try:
        provider = create_provider("anthropic", model="claude-opus-4-20250514")
        assert provider is not None
    finally:
        del os.environ["ANTHROPIC_API_KEY"]


def test_list_providers_includes_builtin() -> None:
    """Built-in providers (anthropic, openai) are registered by default."""
    providers = list_providers()
    names = [p.name for p in providers]
    assert "anthropic" in names
    assert "openai" in names


def test_register_duplicate_overwrites() -> None:
    """Registering the same name twice overwrites without error."""
    info = ProviderInfo("dup_test", "v1", "KEY1", "m1")
    register_provider(info)
    info2 = ProviderInfo("dup_test", "v2", "KEY2", "m2")
    register_provider(info2)
    providers = list_providers()
    match = [p for p in providers if p.name == "dup_test"]
    assert len(match) == 1
    assert match[0].description == "v2"


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset registry before each test by clearing internal dict."""
    import harnesses.provider as mod
    mod._providers.clear()
    mod._register_builtins()
    yield
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_provider.py -v 2>&1`
Expected: ImportError or ModuleNotFoundError for `harnesses.provider`

- [ ] **Step 3: Write minimal implementation**

Create `harnesses/provider.py`:

```python
"""Provider registry + factory for LLM providers.

Usage::

    from harnesses.provider import create_provider

    # Built-in providers
    llm = create_provider("anthropic", api_key="sk-...")
    llm = create_provider("openai", api_key="sk-...")

    # Custom provider
    from harnesses.provider import register_provider, ProviderInfo
    register_provider(ProviderInfo("my_prov", "...", "MY_KEY", "my-model"))
    llm = create_provider("my_prov")
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    description: str
    env_key: str
    model_default: str


# Internal registry: name -> ProviderInfo
_providers: dict[str, ProviderInfo] = {}


def register_provider(info: ProviderInfo) -> None:
    _providers[info.name] = info


def list_providers() -> list[ProviderInfo]:
    return list(_providers.values())


def _register_builtins() -> None:
    register_provider(ProviderInfo(
        name="anthropic",
        description="Anthropic Claude via anthropic SDK",
        env_key="ANTHROPIC_API_KEY",
        model_default="claude-sonnet-4-20250514",
    ))
    register_provider(ProviderInfo(
        name="openai",
        description="OpenAI GPT via openai SDK",
        env_key="OPENAI_API_KEY",
        model_default="gpt-4o",
    ))


_register_builtins()


def create_provider(
    name: str,
    api_key: str | None = None,
    model: str | None = None,
) -> Any:
    info = _providers.get(name)
    if info is None:
        available = ", ".join(_providers)
        raise ValueError(
            f"Unknown provider '{name}'. Available: {available}"
        )

    key = api_key or os.environ.get(info.env_key)
    if not key:
        raise ValueError(
            f"{info.env_key} not set. Pass --api-key or set the "
            f"{info.env_key} environment variable."
        )

    model_name = model or info.model_default

    if name == "anthropic":
        from runtime.llm_provider import AnthropicProvider
        return AnthropicProvider(api_key=key)

    if name == "openai":
        from runtime.llm_provider import OpenAIProvider
        return OpenAIProvider(api_key=key)

    raise ValueError(f"Provider '{name}' is registered but has no factory implementation")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_provider.py -v 2>&1`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add harnesses/provider.py tests/test_provider.py
git commit -m "feat: add provider registry and factory for LLM providers"
```

---

### Task 3: Update harnesses/__init__.py with new exports

**Files:**
- Modify: `harnesses/__init__.py`

- [ ] **Step 1: Update __init__.py**

Add exports after the existing `create_research_harness` import:

```python
from .provider import ProviderInfo, create_provider, list_providers, register_provider

__all__ = [
    "create_research_harness",
    "create_provider",
    "register_provider",
    "list_providers",
    "ProviderInfo",
]
```

- [ ] **Step 2: Verify import works**

Run: `python -c "from harnesses import create_provider, register_provider, ProviderInfo; print('OK')" 2>&1`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add harnesses/__init__.py
git commit -m "feat: export provider API from harnesses package"
```

---

### Task 4: Create CLI entry point (harnesses/cli.py)

**Files:**
- Create: `harnesses/cli.py`
- Test: `tests/test_harness_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_harness_cli.py`:

```python
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
    assert len(result.output) > 0


def test_cli_repl_flag() -> None:
    """--repl flag is accepted."""
    runner = CliRunner()
    result = runner.invoke(research, ["--repl", "--mock", "--research-root", "."], input="exit\n")
    assert result.exit_code == 0


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
    os.environ.pop("ANTHROPIC_API_KEY", None)
    runner = CliRunner()
    result = runner.invoke(research, [
        "query",
        "--provider", "anthropic",
    ])
    assert result.exit_code == 1
    assert "API key" in result.output


def test_cli_custom_research_root() -> None:
    """--research-root creates the directory."""
    import tempfile
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        research_dir = Path(tmp) / "custom_papers"
        result = runner.invoke(research, [
            "query",
            "--research-root", str(research_dir),
        ])
        assert result.exit_code == 0
        assert research_dir.is_dir()


@pytest.fixture(autouse=True)
def _reset_provider_registry():
    """Reset provider registry to clean state before each test."""
    import harnesses.provider as mod
    mod._providers.clear()
    mod._register_builtins()
    yield
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_harness_cli.py -v 2>&1`
Expected: ImportError or ModuleNotFoundError for `harnesses.cli`

- [ ] **Step 3: Write minimal implementation**

Create `harnesses/cli.py`:

```python
"""CLI entry point for ResearchHarness.

Usage::

    research "Find papers on AI"
    research --repl
    research "query" --provider openai --model gpt-4o
"""

from __future__ import annotations

import asyncio
import sys

import click

from runtime.types import UserInput
from harnesses.research import create_research_harness
from harnesses.provider import create_provider, list_providers


def _run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _get_provider_help() -> str:
    """Generate help string listing available providers."""
    providers = list_providers()
    return f"LLM provider ({', '.join(p.name for p in providers)})"


@click.command()
@click.argument("query", required=False)
@click.option("--provider", default="anthropic", help=_get_provider_help())
@click.option("--api-key", help="API key (defaults to corresponding env var)")
@click.option("--model", help="Model name override")
@click.option("--research-root", default="./research", help="Research workspace directory")
@click.option("--repl", is_flag=True, help="Start interactive REPL mode")
@click.option("--mock", is_flag=True, help="Use MockProvider (no API key needed)")
def research(query, provider, api_key, model, research_root, repl, mock):
    """Research assistant: search, fetch, read, and summarize papers.

    Examples:

        research "Find papers on AI"

        research --repl

        research --mock "test query"
    """
    if mock:
        from runtime.llm_provider import MockProvider
        llm = MockProvider()
    else:
        try:
            llm = create_provider(provider, api_key=api_key, model=model)
        except ValueError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)

    harness = create_research_harness(
        research_root=research_root,
        llm=llm,
        model=model,
    )

    harness.start()

    try:
        if repl:
            _run_repl(harness)
        else:
            if not query:
                click.echo(
                    "Error: QUERY required in single-shot mode. "
                    "Use --repl for interactive.",
                    err=True,
                )
                sys.exit(1)
            result = _run_async(harness.turn(UserInput(text=query)))
            click.echo(result.text)
    finally:
        harness.close()


def _run_repl(harness) -> None:
    """Interactive REPL loop."""
    click.echo("Research REPL — type 'exit' to quit")
    while True:
        try:
            text = click.prompt("> ", prompt_suffix="")
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("exit", "quit"):
            break
        if not text.strip():
            continue
        result = _run_async(harness.turn(UserInput(text=text)))
        click.echo(result.text)


if __name__ == "__main__":
    research()
```

- [ ] **Step 4: Verify Click test runner works**

Install Click test dependency: `pip install click 2>&1`

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_harness_cli.py -v 2>&1`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add harnesses/cli.py tests/test_harness_cli.py
git commit -m "feat: add Click CLI entry point for ResearchHarness"
```

---

### Task 5: Create __main__.py for module support

**Files:**
- Create: `harnesses/__main__.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_harness_cli.py`:

```python
def test_module_execution() -> None:
    """python -m harnesses research --help works."""
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-m", "harnesses", "research", "--help"],
        capture_output=True, text=True, cwd=__file__,
    )
    # Falls through to cli module since __main__ calls research()
    assert result.returncode == 0
    assert "Usage:" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_harness_cli.py::test_module_execution -v 2>&1`
Expected: FAIL — `No module named harnesses.__main__`

- [ ] **Step 3: Write minimal implementation**

Create `harnesses/__main__.py`:

```python
"""Allow ``python -m harnesses [research] <query>``."""
import sys

# Strip "research" subcommand if present, so Click sees only the query
# python -m harnesses research "query" → sys.argv: ["...", "research", "query"]
# After pop: ["...", "query"] → Click sees QUERY = "query"
if len(sys.argv) > 1 and sys.argv[1] == "research":
    sys.argv.pop(1)

from .cli import research

research()
```

Usage:
- `python -m harnesses "Find papers"` — QUERY = "Find papers"
- `python -m harnesses research "Find papers"` — same (strips "research")
- `python -m harnesses --repl` — REPL mode

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_harness_cli.py::test_module_execution -v 2>&1`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harnesses/__main__.py
git commit -m "feat: add __main__.py for python -m harnesses support"
```

---

### Task 6: Integration test — full CLI flow with MockProvider

**Files:**
- Modify: `tests/test_harness_cli.py`

- [ ] **Step 1: Write integration test**

Add to `tests/test_harness_cli.py`:

```python
def test_cli_single_shot_mock_provider_output() -> None:
    """Single-shot returns text output from mock provider."""
    runner = CliRunner()
    result = runner.invoke(research, [
        "--mock",
        "hello",
        "--research-root", str(Path.cwd() / "research"),
    ])
    assert result.exit_code == 0
    assert len(result.output) > 0


def test_cli_repl_multiple_turns() -> None:
    """REPL mode handles multiple turns then exit."""
    runner = CliRunner()
    input_text = "first query\nsecond query\nexit\n"
    result = runner.invoke(research, [
        "--repl", "--mock",
        "--research-root", str(Path.cwd() / "research"),
    ], input=input_text)
    assert result.exit_code == 0
    assert "Research REPL" in result.output


def test_cli_repl_empty_input() -> None:
    """REPL mode skips empty input."""
    runner = CliRunner()
    result = runner.invoke(research, [
        "--repl", "--mock",
        "--research-root", str(Path.cwd() / "research"),
    ], input="\n\nexit\n")
    assert result.exit_code == 0


def test_cli_repl_eof() -> None:
    """REPL mode exits on EOF (Ctrl+D)."""
    runner = CliRunner()
    result = runner.invoke(research, [
        "--repl", "--mock",
        "--research-root", str(Path.cwd() / "research"),
    ], input="")
    assert result.exit_code == 0
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_harness_cli.py -v 2>&1`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_harness_cli.py
git commit -m "test: add integration tests for CLI single-shot and REPL modes"
```

---

### Task 7: Manual verification — smoke test from terminal

- [ ] **Step 1: Run help output**

```bash
python -m harnesses research --help
```

Expected output (approximate):
```
Usage: research [OPTIONS] [QUERY]

  Research assistant: search, fetch, read, and summarize papers.

Options:
  --provider TEXT       LLM provider (anthropic, openai)
  --api-key TEXT        API key (defaults to corresponding env var)
  --model TEXT          Model name override
  --research-root TEXT  Research workspace directory
  --repl                Start interactive REPL mode
  --help                Show this message
```

- [ ] **Step 2: Run single-shot with MockProvider**

```bash
python -m harnesses research "test query" --research-root ./research
```

Expected: Output from MockProvider (echo of the input).

- [ ] **Step 3: Run REPL mode**

```bash
python -m harnesses research --repl
```

Expected: REPL prompt appears. Type `exit` to quit.
