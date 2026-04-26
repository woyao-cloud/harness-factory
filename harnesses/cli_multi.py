"""CLI entry point for multi-agent research.

Usage::

    multi-research "Find papers on AI transformers"
    multi-research --repl
    multi-research --mock "test query"
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import click

from harnesses.multiagent import create_multiagent_coordinator
from harnesses.provider import create_provider


def _run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _save_result(research_root: str, text: str, topic: str = "") -> str | None:
    """Save final result text to ``{research_root}/research-output-{timestamp}.md``."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if topic:
        safe_topic = "".join(c if c.isalnum() or c in " _-" else "_" for c in topic)[:40]
        filename = f"{safe_topic}_{timestamp}.md"
    else:
        filename = f"research-output_{timestamp}.md"

    root = Path(research_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / filename

    try:
        path.write_text(text, encoding="utf-8")
        return str(path)
    except OSError as exc:
        click.echo(f"Warning: failed to save result to {path}: {exc}", err=True)
        return None


@click.command()
@click.argument("query", required=False)
@click.option("--provider", default="ollama", help="LLM provider (ollama, anthropic, openai)")
@click.option("--api-key", help="API key (defaults to corresponding env var)")
@click.option("--model", help="Model name override")
@click.option("--research-root", default="./research", help="Research workspace directory")
@click.option("--repl", is_flag=True, help="Start interactive REPL mode")
@click.option("--mock", is_flag=True, help="Use MockProvider (no API key needed)")
@click.option("--verbose", is_flag=True, help="Show detailed agent progress logs")
@click.option("--max-iterations", default=3, help="Maximum review cycles")
def multi_research(query, provider, api_key, model, research_root, repl, mock, verbose, max_iterations):
    """Multi-agent research: plan → execute → review.

    Uses Planner, Worker, and Review agents to collaboratively complete research tasks.
    """
    if mock:
        from runtime.llm_provider import MockProvider
        llm = MockProvider()
    else:
        try:
            llm = create_provider(provider, api_key=api_key)
        except ValueError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)

    coordinator = create_multiagent_coordinator(
        research_root=research_root,
        llm=llm,
        model=model,
        max_iterations=max_iterations,
        verbose=verbose,
    )

    try:
        if repl:
            _run_repl(coordinator, research_root)
        else:
            if not query:
                click.echo(
                    "Error: QUERY required in single-shot mode. "
                    "Use --repl for interactive.",
                    err=True,
                )
                sys.exit(1)
            result = _run_async(coordinator.run(query))
            click.echo(result.text)
            saved = _save_result(research_root, result.text, query)
            click.echo("── Done ──", err=True)
            if saved:
                click.echo(f"Result saved to {saved}", err=True)
    finally:
        pass


def _run_repl(coordinator, research_root: str = "./research") -> None:
    """Interactive REPL loop."""
    click.echo("Multi-Agent Research REPL — type 'exit' to quit")
    try:
        while True:
            try:
                text = click.prompt("> ", prompt_suffix="")
            except (EOFError, KeyboardInterrupt):
                click.echo("")
                break
            if text.lower() in ("exit", "quit"):
                break
            if not text.strip():
                continue
            try:
                result = _run_async(coordinator.run(text))
                _save_result(research_root, result.text, text)
                click.echo(result.text)
            except Exception as exc:
                click.echo(f"Error: {exc}", err=True)
    finally:
        pass


if __name__ == "__main__":
    multi_research()
