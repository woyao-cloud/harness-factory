"""AgentBase — abstract base for all agent types.

Each agent wraps a Pipeline instance with its own Session, SecurityGate,
and tool set, sharing the LLM provider with other agents.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from runtime.llm_provider import LLMProvider, MockProvider
from runtime.message_bus import MessageBus
from runtime.pipeline import AutoPipeline, PipelineConfig
from runtime.security import SecurityConfig, SecurityGate
from runtime.session import Session, SessionConfig
from runtime.types import InferenceConfig

from .types import AgentContext, AgentResult, AgentRole

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for a single agent."""

    role: AgentRole = AgentRole.WORKER
    system_prompt: str = ""
    model: str = "deepseek-v4-flash:cloud"
    max_tool_rounds: int = 25
    security_default_decision: str = "ask"
    security_require_approval: tuple[str, ...] = ("bash", "exec", "write", "edit")
    max_tokens: int = 4096
    temperature: float = 0.7


class AgentBase(ABC):
    """Abstract base for a single agent in the multi-agent system.

    Each agent wraps:
    - ``AutoPipeline`` for autonomous tool-loop execution
    - ``Session`` for conversation context
    - ``SecurityGate`` for tool approval

    Agents share the same ``LLMProvider`` but maintain independent sessions.
    """

    def __init__(
        self,
        config: AgentConfig,
        llm: LLMProvider,
        tools: list[Any] | None = None,
    ) -> None:
        self._cfg = config
        self._llm = llm
        self._bus = MessageBus()
        self._session = Session(
            SessionConfig(
                session_id=f"{config.role.name.lower()}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
                total_budget=128000,
            ),
            self._bus,
        )

        # Security gate
        from runtime.security import Decision
        _dmap = {
            "allow": Decision.ALLOW,
            "ask": Decision.ASK,
            "deny": Decision.DENY,
        }
        default_decision = _dmap.get(
            config.security_default_decision.lower(), Decision.ASK
        )
        self._security = SecurityGate(
            SecurityConfig(
                default_decision=default_decision,
                require_approval_for=config.security_require_approval,
            )
        )

        # Pipeline
        inf_cfg = InferenceConfig(
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            system_prompt=config.system_prompt,
        )
        pipeline_cfg = PipelineConfig(
            max_tool_rounds=config.max_tool_rounds,
            inference_config=inf_cfg,
            tool_as_messages=True,
        )
        self._pipeline = AutoPipeline(
            pipeline_cfg,
            self._session,
            self._llm,
            self._bus,
            self._security,
        )

        # Register tools
        if tools:
            for tool in tools:
                self._pipeline.register_tool(tool.definition, tool.safe_execute)

        # Start session immediately — stays alive until explicit close
        self._session.start()

    @property
    def role(self) -> AgentRole:
        return self._cfg.role

    @property
    def session(self) -> Session:
        return self._session

    @property
    def pipeline(self) -> AutoPipeline:
        return self._pipeline

    def reset(self) -> None:
        """Reset session context — allows the agent to be re-used for revision."""
        cm = self._session.context_manager
        cm.reset()

    def close(self) -> None:
        """Close the agent's session."""
        self._session.close()

    @abstractmethod
    async def run(self, context: AgentContext) -> AgentResult:
        """Run the agent with the given context and return results."""
        ...
