"""LLM provider abstraction — protocol + implementations.

Protocol::

    class LLMProvider(Protocol):
        async def complete(
            self,
            messages: list[dict],
            tools: list[dict],
            config: InferenceConfig,
        ) -> InferenceResult: ...

Built-in implementations:

* ``AnthropicProvider`` — Claude via anthropic SDK
* ``OpenAIProvider`` — GPT via openai SDK
* ``MockProvider`` — deterministic responses for testing
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from .types import InferenceConfig, InferenceResult, ToolCall, Usage

logger = logging.getLogger(__name__)


# ── Protocol ─────────────────────────────────────────────────────────────────


class LLMProvider(Protocol):
    """Interface for LLM inference.

    Implementations must handle:
    * Message formatting (system/user/assistant/tool_result)
    * Tool definitions in the provider's native format
    * Streaming (optional, via ``stream_complete``)
    * Error handling with meaningful messages
    """

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict],
        config: InferenceConfig,
    ) -> InferenceResult:
        """Single inference call.

        Args:
            messages: List of message dicts with ``role`` and ``content`` keys.
            tools: List of tool definitions in the provider's format.
            config: Inference parameters (model, temperature, etc.).

        Returns:
            ``InferenceResult`` with text and/or tool_calls.
        """
        ...


# ── Anthropic implementation ─────────────────────────────────────────────────


class AnthropicProvider:
    """Claude via the anthropic Python SDK.

    Requires ``anthropic`` to be installed and ``ANTHROPIC_API_KEY`` set.
    """

    MODEL_MAP = {
        "claude-sonnet-4-20250514": "claude-sonnet-4-20250514",
        "claude-sonnet-4": "claude-sonnet-4-20250514",
        "claude-opus-4-20250514": "claude-opus-4-20250514",
        "claude-opus-4": "claude-opus-4-20250514",
        "claude-haiku-4-20250514": "claude-haiku-4-20250514",
        "claude-haiku-4": "claude-haiku-4-20250514",
        "claude-sonnet-4-6": "claude-sonnet-4-20250514",
        "claude-opus-4-6": "claude-opus-4-20250514",
    }

    def __init__(self, api_key: str | None = None) -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict],
        config: InferenceConfig,
    ) -> InferenceResult:
        model = self.MODEL_MAP.get(config.model, config.model)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "messages": [m for m in messages if m.get("role") != "system"],
        }

        if tools:
            kwargs["tools"] = tools

        if config.system_prompt:
            kwargs["system"] = config.system_prompt

        if config.stop_sequences:
            kwargs["stop_sequences"] = list(config.stop_sequences)

        # Extended thinking
        if config.thinking:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": config.thinking_budget or config.max_tokens // 2,
            }

        response = await self._client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        tool_name=block.name,
                        params=block.input if isinstance(block.input, dict) else {},
                    )
                )

        return InferenceResult(
            text="".join(text_parts),
            tool_calls=tuple(tool_calls),
            stop_reason=response.stop_reason or "end_turn",
            usage=Usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            model=model,
        )


# ── OpenAI implementation ────────────────────────────────────────────────────


class OpenAIProvider:
    """GPT via the openai Python SDK.

    Requires ``openai`` to be installed and ``OPENAI_API_KEY`` set.
    """

    MODEL_MAP = {
        "gpt-4o": "gpt-4o",
        "gpt-4o-mini": "gpt-4o-mini",
        "o3-mini": "o3-mini",
    }

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict],
        config: InferenceConfig,
    ) -> InferenceResult:
        model = self.MODEL_MAP.get(config.model, config.model)

        # Merge system into messages for OpenAI format
        openai_messages: list[dict] = []
        if config.system_prompt:
            openai_messages.append({"role": "system", "content": config.system_prompt})
        openai_messages.extend(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "messages": openai_messages,
        }

        if tools:
            kwargs["tools"] = tools

        if config.stop_sequences:
            kwargs["stop"] = list(config.stop_sequences)

        response = await self._client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    params = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    params = {}
                tool_calls.append(
                    ToolCall(tool_name=tc.function.name, params=params)
                )

        return InferenceResult(
            text=message.content or "",
            tool_calls=tuple(tool_calls),
            stop_reason=choice.finish_reason or "stop",
            usage=Usage(
                input_tokens=response.usage.prompt_tokens if response.usage else 0,
                output_tokens=response.usage.completion_tokens if response.usage else 0,
            ),
            model=model,
        )


# ── Mock provider for testing ────────────────────────────────────────────────


class MockProvider:
    """Deterministic mock provider for testing pipelines.

    Returns text or tool calls based on a configurable response schedule.
    """

    def __init__(self, responses: list[InferenceResult] | None = None) -> None:
        self._responses = list(responses or [])
        self._call_count = 0
        self._last_messages: list[dict] = []
        self._last_tools: list[dict] = []

    def add_response(self, result: InferenceResult) -> None:
        self._responses.append(result)

    @property
    def call_count(self) -> int:
        return self._call_count

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict],
        config: InferenceConfig,
    ) -> InferenceResult:
        self._last_messages = messages
        self._last_tools = tools
        self._call_count += 1

        if self._call_count <= len(self._responses):
            return self._responses[self._call_count - 1]

        # Default: echo response
        last = messages[-1]["content"] if messages else ""
        return InferenceResult(
            text=f"[mock] received: {last[:80]}",
            stop_reason="end_turn",
            model="mock",
        )
