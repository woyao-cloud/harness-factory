"""CLI entry point for multi-agent coding.

Usage::

    multi-coding "Build a Python calculator app"
    multi-coding --repl
    multi-coding --mock "test coding task"
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

import click

from runtime.agents.types import Plan

from harnesses.coding import create_coding_coordinator
from harnesses.provider import create_provider


def _run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _confirm_plan(plan: "Plan") -> bool:
    """Show the plan and ask the user to confirm.

    Returns ``True`` to proceed, ``False`` to cancel.
    """
    click.echo("")
    click.echo("── Plan ──", err=True)
    click.echo(f"  Goal: {plan.goal}", err=True)
    if plan.context:
        click.echo(f"  Context: {plan.context[:200]}", err=True)
    click.echo("", err=True)
    click.echo("  Tasks:", err=True)
    for i, item in enumerate(plan.items, 1):
        deps = f" (depends on: {', '.join(item.depends_on)})" if item.depends_on else ""
        click.echo(f"    {i}. {item.id}: {item.description[:100]}{deps}", err=True)
    click.echo("", err=True)
    try:
        return click.confirm("Proceed with this plan?", default=True, err=True)
    except (EOFError, KeyboardInterrupt):
        click.echo("", err=True)
        return False


def _save_result(output_dir: str, text: str, topic: str = "") -> str | None:
    """Save final result text to ``{output_dir}/coding-output-{timestamp}.md``."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if topic:
        safe_topic = "".join(c if c.isalnum() or c in " _-" else "_" for c in topic)[:40]
        filename = f"{safe_topic}_{timestamp}.md"
    else:
        filename = f"coding-output_{timestamp}.md"

    root = Path(output_dir).resolve()
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
@click.option("--output-dir", default="./coding", help="Output directory for generated code")
@click.option("--repl", is_flag=True, help="Start interactive REPL mode")
@click.option("--mock", is_flag=True, help="Use MockProvider (no API key needed)")
@click.option("--verbose", is_flag=True, help="Show detailed agent progress logs")
@click.option("--max-iterations", default=3, help="Maximum review cycles")
@click.option("--no-confirm", is_flag=True, default=False, help="Skip plan confirmation prompt")
def multi_coding(query, provider, api_key, model, output_dir, repl, mock, verbose, max_iterations, no_confirm):
    """Multi-agent coding: plan -> write code -> review.

    Uses Planner, Worker, and Review agents to collaboratively complete coding tasks.
    Generated code files are written to OUTPUT_DIR.
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

    # Enable debug logging when verbose
    if verbose:
        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)s [%(name)s] %(message)s",
        )

    coordinator = create_coding_coordinator(
        output_dir=output_dir,
        llm=llm,
        **({"model": model} if model else {}),
        max_iterations=max_iterations,
        verbose=verbose,
    )

    try:
        if repl:
            _run_repl(coordinator, output_dir)
        else:
            if not query:
                click.echo(
                    "Error: QUERY required in single-shot mode. "
                    "Use --repl for interactive.",
                    err=True,
                )
                sys.exit(1)
            confirm_fn = None if no_confirm else _confirm_plan
            result = _run_async(coordinator.run(query, confirm_plan=confirm_fn))
            if result.error == "Plan cancelled by user":
                click.echo("Plan cancelled.", err=True)
                sys.exit(0)
            click.echo(result.text)
            saved = _save_result(output_dir, result.text, query)
            click.echo("── Done ──", err=True)
            if saved:
                click.echo(f"Result saved to {saved}", err=True)
    finally:
        pass


def _run_repl(coordinator, output_dir: str = "./coding") -> None:
    """Interactive REPL loop."""
    click.echo("Multi-Agent Coding REPL -- type 'exit' to quit")
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
                result = _run_async(coordinator.run(text, confirm_plan=_confirm_plan))
                if result.error == "Plan cancelled by user":
                    click.echo("Plan cancelled.", err=True)
                    continue
                _save_result(output_dir, result.text, text)
                click.echo(result.text)
            except Exception as exc:
                click.echo(f"Error: {exc}", err=True)
    finally:
        pass


if __name__ == "__main__":
    multi_coding()
