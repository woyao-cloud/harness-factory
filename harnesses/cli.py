"""CLI entry point for ResearchHarness.

Usage::

    research "Find papers on AI"
    research --repl
    research --mock "test query"
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import click

from runtime.types import UserInput
from harnesses.research import create_research_harness
from harnesses.provider import create_provider


def _run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _show_usage(harness) -> None:
    """Display token usage summary from the session."""
    usage = harness.total_usage
    if usage.input_tokens or usage.output_tokens:
        click.echo(
            f"── Tokens: {usage.input_tokens} in  |  {usage.output_tokens} out  |  "
            f"{usage.input_tokens + usage.output_tokens} total ──",
            err=True,
        )


def _save_result(research_root: str, text: str, topic: str = "") -> str | None:
    """Save final result text to ``{research_root}/research-output-{timestamp}.md``.

    Returns the file path written, or ``None`` on failure.
    """
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
            llm = create_provider(provider, api_key=api_key)
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
            _run_repl(harness, research_root)
        else:
            if not query:
                click.echo(
                    "Error: QUERY required in single-shot mode. "
                    "Use --repl for interactive.",
                    err=True,
                )
                sys.exit(1)
            result = _run_async(harness.turn(UserInput(text=query)))
            saved = _save_result(research_root, result.text, query)
            click.echo(result.text)
            if saved:
                click.echo(f"Result saved to {saved}", err=True)
            _show_usage(harness)
    finally:
        harness.close()


def _run_repl(harness: "HarnessRuntime", research_root: str = "./research") -> None:
    """Interactive REPL loop."""
    click.echo("Research REPL — type 'exit' to quit")
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
                result = _run_async(harness.turn(UserInput(text=text)))
                _save_result(research_root, result.text, text)
                click.echo(result.text)
            except Exception as exc:
                click.echo(f"Error: {exc}", err=True)
    finally:
        _show_usage(harness)


if __name__ == "__main__":
    research()
