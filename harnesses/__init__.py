"""Harness Factory — pre-configured domain-specific HarnessRuntime factories.

Each factory function creates a fully wired ``HarnessRuntime`` with:
* Domain-appropriate tool selection
* Curated system prompt
* Sensible security defaults
* Ready-to-use pipeline

Usage::

    from harnesses import create_research_harness

    harness = create_research_harness(research_root="./papers")
    harness.start()
    result = await harness.turn(UserInput(text="Find papers on topic X"))
    harness.close()
"""

from .provider import ProviderInfo, create_provider, list_providers, register_provider
from .research import create_research_harness

__all__ = [
    "create_research_harness",
    "create_provider",
    "register_provider",
    "list_providers",
    "ProviderInfo",
]
