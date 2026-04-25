"""Allow ``python -m harnesses [research]``.

Usage::

    python -m harnesses "Find papers"
    python -m harnesses research "Find papers"
    python -m harnesses --repl
"""

import sys

# Strip "research" subcommand if present so Click sees the query
# python -m harnesses research "query" → ["...", "research", "query"]
if len(sys.argv) > 1 and sys.argv[1] == "research":
    sys.argv.pop(1)

from .cli import research

research()
