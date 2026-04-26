"""Allow ``python -m harnesses`` for single-agent and multi-agent research.

Usage::

    # Single-agent (existing)
    python -m harnesses "Find papers"
    python -m harnesses research "Find papers"
    python -m harnesses --repl

    # Multi-agent (new)
    python -m harnesses multi-research "Find papers"
    python -m harnesses multi-research --repl
"""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "multi-coding":
    sys.argv.pop(1)
    from .cli_coding import multi_coding
    multi_coding()
elif len(sys.argv) > 1 and sys.argv[1] == "multi-research":
    sys.argv.pop(1)
    from .cli_multi import multi_research
    multi_research()
else:
    # Strip "research" subcommand if present so Click sees the query
    if len(sys.argv) > 1 and sys.argv[1] == "research":
        sys.argv.pop(1)

    from .cli import research
    research()
