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
from typing import Any, Callable

from runtime.llm_provider import LLMProvider as _LLMProvider


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    description: str
    env_key: str
    model_default: str


# Internal registry: name -> (ProviderInfo, optional factory callable)
_providers: dict[str, tuple[ProviderInfo, Callable[[str], Any] | None]] = {}


__all__ = [
    "ProviderInfo",
    "create_provider",
    "list_providers",
    "register_provider",
]


def register_provider(
    info: ProviderInfo,
    factory: Callable[[str], Any] | None = None,
) -> None:
    _providers[info.name] = (info, factory)


def list_providers() -> list[ProviderInfo]:
    return [info for info, _ in _providers.values()]


def _register_builtins() -> None:
    from runtime.llm_provider import AnthropicProvider, OpenAIProvider

    register_provider(
        ProviderInfo("anthropic", "Anthropic Claude via anthropic SDK",
                      "ANTHROPIC_API_KEY", "claude-sonnet-4-20250514"),
        lambda key: AnthropicProvider(api_key=key),
    )
    register_provider(
        ProviderInfo("openai", "OpenAI GPT via openai SDK",
                      "OPENAI_API_KEY", "gpt-4o"),
        lambda key: OpenAIProvider(api_key=key),
    )


_register_builtins()


def create_provider(
    name: str,
    api_key: str | None = None,
) -> _LLMProvider:
    entry = _providers.get(name)
    if entry is None:
        available = ", ".join(_providers)
        raise ValueError(
            f"Unknown provider '{name}'. Available: {available}"
        )

    info, factory = entry
    key = api_key or os.environ.get(info.env_key)
    if not key:
        raise ValueError(
            f"{info.env_key} not set. Pass --api-key or set the "
            f"{info.env_key} environment variable to provide an API key."
        )

    if factory is not None:
        return factory(key)

    raise ValueError(
        f"Provider '{name}' is registered but has no factory implementation"
    )
