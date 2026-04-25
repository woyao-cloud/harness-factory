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
            f"{info.env_key} environment variable to provide an API key."
        )

    model_name = model or info.model_default

    if name == "anthropic":
        from runtime.llm_provider import AnthropicProvider
        return AnthropicProvider(api_key=key)

    if name == "openai":
        from runtime.llm_provider import OpenAIProvider
        return OpenAIProvider(api_key=key)

    raise ValueError(f"Provider '{name}' is registered but has no factory implementation")
