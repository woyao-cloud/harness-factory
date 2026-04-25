"""CLI entry point for ResearchHarness.

Usage::

    research "Find papers on AI"
    research --repl
    research --mock "test query"
"""

from __future__ import annotations

import asyncio
import sys

import click

from runtime.types import UserInput
from harnesses.research import create_research_harness
from harnesses.provider import create_provider


def _run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


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


def _run_repl(harness: "HarnessRuntime") -> None:
    """Interactive REPL loop."""
    click.echo("Research REPL — type 'exit' to quit")
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
            click.echo(result.text)
        except Exception as exc:
            click.echo(f"Error: {exc}", err=True)


if __name__ == "__main__":
    research()
