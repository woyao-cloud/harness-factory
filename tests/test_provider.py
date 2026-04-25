"""Tests for harnesses/provider.py — registry and factory."""

from __future__ import annotations

import os

import pytest

from harnesses.provider import ProviderInfo, create_provider, list_providers, register_provider
from runtime.llm_provider import OpenAIProvider


def test_register_and_list() -> None:
    """Register a provider and verify it appears in list."""
    info = ProviderInfo("test_prov", "Test provider", "TEST_KEY", "test-model")
    register_provider(info)
    providers = list_providers()
    names = [p.name for p in providers]
    assert "test_prov" in names


def test_create_anthropic_provider() -> None:
    """create_provider('anthropic') returns AnthropicProvider."""
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-fake-key-12345"
    try:
        provider = create_provider("anthropic")
        assert provider is not None
        assert type(provider).__name__ == "AnthropicProvider"
    finally:
        del os.environ["ANTHROPIC_API_KEY"]


def test_create_openai_provider() -> None:
    """create_provider('openai') returns OpenAIProvider."""
    os.environ["OPENAI_API_KEY"] = "sk-test-fake-key-12345"
    try:
        provider = create_provider("openai")
        assert provider is not None
        assert type(provider).__name__ == "OpenAIProvider"
    finally:
        del os.environ["OPENAI_API_KEY"]


def test_create_provider_with_api_key_arg() -> None:
    """API key passed as arg is used instead of env var."""
    old = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        provider = create_provider("anthropic", api_key="sk-arg-key")
        assert provider is not None
        assert type(provider).__name__ == "AnthropicProvider"
    finally:
        if old is not None:
            os.environ["ANTHROPIC_API_KEY"] = old


def test_create_provider_unknown() -> None:
    """Unknown provider name raises ValueError."""
    with pytest.raises(ValueError, match="Unknown provider"):
        create_provider("nonexistent")


def test_create_provider_missing_key() -> None:
    """Missing API key (no arg, no env) raises ValueError."""
    old = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        with pytest.raises(ValueError, match="API key"):
            create_provider("anthropic")
    finally:
        if old is not None:
            os.environ["ANTHROPIC_API_KEY"] = old


def test_create_ollama_provider() -> None:
    """create_provider('ollama') returns OpenAIProvider with Ollama base_url."""
    provider = create_provider("ollama", api_key="ignored")
    assert provider is not None
    assert type(provider).__name__ == "OpenAIProvider"
    assert "localhost:11434" in str(provider._client.base_url)


def test_openai_provider_with_base_url() -> None:
    """OpenAIProvider accepts optional base_url parameter."""
    provider = OpenAIProvider(api_key="sk-test", base_url="http://custom:8080/v1")
    assert "custom:8080" in str(provider._client.base_url)


def test_list_providers_includes_builtin() -> None:
    """Built-in providers (ollama, anthropic, openai) are registered by default."""
    providers = list_providers()
    names = [p.name for p in providers]
    assert "ollama" in names
    assert "anthropic" in names
    assert "openai" in names


def test_register_duplicate_overwrites() -> None:
    """Registering the same name twice overwrites without error."""
    info = ProviderInfo("dup_test", "v1", "KEY1", "m1")
    register_provider(info)
    info2 = ProviderInfo("dup_test", "v2", "KEY2", "m2")
    register_provider(info2)
    providers = list_providers()
    match = [p for p in providers if p.name == "dup_test"]
    assert len(match) == 1
    assert match[0].description == "v2"


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset registry before each test by clearing internal dict."""
    import harnesses.provider as mod
    mod._providers.clear()
    mod._register_builtins()
    yield
