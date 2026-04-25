# Research Harness CLI Design

**Date:** 2026-04-25
**Status:** Draft
**Author:** Brainstorming session

## Overview

Add a CLI entry point to the ResearchHarness so it can be run directly from the terminal — both as a single-shot command and as an interactive REPL. The CLI supports multiple LLM providers via an extensible provider registry.

## Architecture

### File Layout

```
harnesses/
  cli.py            ← Click CLI entry point, subcommand registration
  provider.py       ← Provider registry + factory
  __main__.py       ← python -m harnesses support
  __init__.py       ← Export create_provider, register_provider

pyproject.toml      ← [project.scripts] for console entry point
```

All CLI/Provider code lives in `harnesses/` — separate from `runtime/` and `tool_registry/`.

### CLI Modes

| Mode | Trigger | Behavior |
|------|---------|----------|
| Single-shot | `research "query"` | Run one turn, print output, exit |
| Interactive | `research --repl` | Enter REPL loop, exit on `exit`/`quit` |

### Provider System

**`harnesses/provider.py`** — registry + factory pattern:

```python
@dataclass(frozen=True)
class ProviderInfo:
    name: str
    description: str
    env_key: str          # e.g. "ANTHROPIC_API_KEY"
    model_default: str    # e.g. "claude-sonnet-4-20250514"
```

- `register_provider(info)` — add a provider to the registry
- `list_providers()` — list registered providers (for help text)
- `create_provider(name, api_key, model)` → `LLMProvider`
  - Looks up registry by name
  - API key: CLI arg > env var > error
  - Model: CLI arg > ProviderInfo.model_default

Built-in providers:

| Name | Class | Env Key | Default Model |
|------|-------|---------|---------------|
| `anthropic` | `AnthropicProvider` | `ANTHROPIC_API_KEY` | `claude-sonnet-4-20250514` |
| `openai` | `OpenAIProvider` | `OPENAI_API_KEY` | `gpt-4o` |

Extensibility — add a custom provider:

```python
from harnesses import register_provider, ProviderInfo
register_provider(ProviderInfo("deepseek", "...", "DEEPSEEK_API_KEY", "deepseek-chat"))
```

### CLI Interface

**Command:** `research [OPTIONS] [QUERY]`

```
Options:
  --provider TEXT     LLM provider (anthropic, openai)  [default: anthropic]
  --api-key TEXT      API key (defaults to env var)
  --model TEXT        Model name override
  --research-root TEXT  Research workspace directory  [default: ./research]
  --repl              Start interactive REPL mode
  --help              Show this message
```

**Single-shot flow:**

```
research "Find papers on transformer architectures"
  → parse args
  → create_provider(provider, api_key, model) → LLMProvider
  → create_research_harness(research_root, llm)
  → harness.start()
  → asyncio.run(harness.turn(UserInput(text=query)))
  → print(result.text)
  → harness.close()
```

**REPL flow:**

```
research --repl
  → create harness (same as above)
  → Research REPL — type 'exit' to quit
  > Find papers on AI
  [LLM response...]
  > Summarize the key findings
  [LLM response...]
  > exit
  → harness.close()
```

### Module Support

`harnesses/__main__.py`:
```python
from .cli import research
research()
```

Usage: `python -m harnesses research "query"`

### Console Script

`pyproject.toml`:
```toml
[project.scripts]
research = "harnesses.cli:research"
```

After `pip install`: `research "Find papers on AI"`

### Dependencies

- **Click** — CLI framework (the only new runtime dependency)
- All other dependencies already exist in the project

### Error Handling

| Scenario | Behavior |
|----------|----------|
| Missing API key | Error + hint: `Error: ANTHROPIC_API_KEY not set. Pass --api-key or set env var.` |
| Invalid provider | Error + list: `Unknown provider 'foo'. Available: anthropic, openai` |
| Network/API error | Catch, print friendly message, exit 1 |
| Single-shot, no query | Error: `QUERY required in single-shot mode. Use --repl for interactive.` |
| Invalid research_root | Auto-created (handled by existing `ensure_dir`) |

### Testing

| Test | What it covers |
|------|---------------|
| Provider registry | register, list, create, duplicate registration |
| Provider factory | anthropic → AnthropicProvider, openai → OpenAIProvider |
| Provider fallback | API key from env var when --api-key omitted |
| CLI single-shot | `research "query"` → correct args parsed |
| CLI REPL flag | `research --repl` → REPL mode activated |
| CLI help | `research --help` → usage output |
| Bad provider | Error message and exit code |
| Missing API key | Error message and exit code |
| Module mode | `python -m harnesses research "query"` works |

### Files Changed

| File | Change |
|------|--------|
| `harnesses/provider.py` | **New** — ProviderInfo, registry, factory |
| `harnesses/cli.py` | **New** — Click CLI with research subcommand |
| `harnesses/__main__.py` | **New** — module entry point |
| `harnesses/__init__.py` | Add exports: create_provider, register_provider, ProviderInfo |
| `pyproject.toml` | Add [project.scripts], Click dependency |
