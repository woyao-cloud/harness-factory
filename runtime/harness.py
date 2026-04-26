"""HarnessRuntime — main orchestrator tying all Core Runtime components.

Integration features:

* **Recovery** — retry + circuit breaker for LLM/tool calls
* **Streaming** — token-by-token output via ``StreamPipeline``
* **Checkpoint** — auto-save session state for crash recovery
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

from .llm_provider import LLMProvider, MockProvider
from .message_bus import EventHandler, MessageBus
from .pipeline import AutoPipeline, InteractivePipeline, Pipeline, PipelineConfig, ToolExecutorFn
from .request_logger import LLMRequestLogger
from .security import SecurityConfig, SecurityGate
from .session import Session, SessionConfig
from .streaming import StreamPipeline
from .checkpoint import AutoSaveManager, CheckpointConfig, CheckpointStore
from .recovery import PipelineRecovery, RecoveryConfig
from .types import (
    HarnessSpec,
    PipelineEvent,
    StepResult,
    ToolDefinition,
    Usage,
    UserInput,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HarnessConfig:
    session_budget: int = 64000
    max_tool_rounds: int = 25
    security_default_decision: str = "allow"
    security_require_approval: tuple[str, ...] = ("bash", "exec")
    pipeline_strategy: str = "interactive"  # interactive | auto | stream
    enable_auto_save: bool = False
    checkpoint_interval: int = 30
    metadata: dict[str, Any] = field(default_factory=dict)


class HarnessRuntime:
    """Main orchestrator — ties Session, Pipeline, LLM, Security together.

    Lifecycle::

        start() → turn() → turn() → ... → close()

    Recovery (optional)::

        harness = HarnessRuntime(spec, llm=llm, recovery=PipelineRecovery())

    Checkpoint (optional)::

        store = CheckpointStore(CheckpointConfig(save_dir="./checkpoints"))
        harness = HarnessRuntime(spec, llm=llm, checkpoint_store=store,
                                  config=HarnessConfig(enable_auto_save=True))

        # On restart:
        harness2 = HarnessRuntime.restore(spec, store, "session_id", llm=llm)
    """

    def __init__(
        self,
        spec: HarnessSpec,
        llm: LLMProvider | None = None,
        config: HarnessConfig | None = None,
        recovery: PipelineRecovery | None = None,
        checkpoint_store: CheckpointStore | None = None,
    ) -> None:
        self._spec = spec
        self._cfg = config or HarnessConfig()

        # Core components
        self._bus = MessageBus()

        self._security = SecurityGate(
            SecurityConfig(
                default_decision="ASK",
                require_approval_for=self._cfg.security_require_approval,
            )
        )

        self._session = Session(
            SessionConfig(
                total_budget=self._cfg.session_budget,
                metadata=dict(spec.metadata),
            ),
            self._bus,
        )

        # LLM provider
        self._llm = llm or MockProvider()

        # Recovery layer
        self._recovery = recovery

        # Pipeline
        self._pipeline = self._build_pipeline()

        # Register spec tools
        for tool_def in spec.tools:
            self._pipeline.register_tool(tool_def, self._default_executor)

        # Checkpoint
        self._checkpoint_store = checkpoint_store
        self._auto_save: AutoSaveManager | None = None
        if checkpoint_store and self._cfg.enable_auto_save:
            self._auto_save = AutoSaveManager(
                checkpoint_store, interval=self._cfg.checkpoint_interval,
            )
            self._bus.on(
                PipelineEvent.PIPELINE_TURN,
                self._auto_save.create_handler(),
            )
            self._bus.on(
                PipelineEvent.SESSION_CLOSED,
                self._auto_save.create_handler(),
            )

        self._closed = False

    def _build_pipeline(self) -> Pipeline:
        """Select pipeline strategy from spec/config."""
        strategy = (self._spec.pipeline_strategy or self._cfg.pipeline_strategy).lower()
        pipeline_cfg = PipelineConfig(
            max_tool_rounds=self._cfg.max_tool_rounds,
            tool_as_messages=True,
        )

        cls_map = {
            "interactive": InteractivePipeline,
            "auto": AutoPipeline,
            "stream": StreamPipeline,
        }
        pipeline_cls = cls_map.get(strategy, InteractivePipeline)

        return pipeline_cls(
            pipeline_cfg,
            self._session,
            self._llm,
            self._bus,
            self._security,
            request_logger=LLMRequestLogger(),
        )

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def session(self) -> Session:
        return self._session

    @property
    def bus(self) -> MessageBus:
        return self._bus

    @property
    def pipeline(self) -> Pipeline:
        return self._pipeline

    @property
    def security(self) -> SecurityGate:
        return self._security

    @property
    def spec(self) -> HarnessSpec:
        return self._spec

    @property
    def recovery(self) -> PipelineRecovery | None:
        return self._recovery

    @property
    def checkpoint_store(self) -> CheckpointStore | None:
        return self._checkpoint_store

    @property
    def total_usage(self) -> "Usage":
        """Accumulated token usage across all turns in this session."""
        return self._session.total_usage

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("Cannot restart a closed harness")
        self._session.start()

    async def turn(self, user_input: UserInput) -> StepResult:
        if self._session.state.name == "CLOSED":
            raise RuntimeError("Session is closed — create a new HarnessRuntime")

        # Recovery-aware execution
        if self._recovery:
            result = await self._recovery.call_with_recovery(
                "pipeline_turn",
                self._pipeline.turn,
                user_input=user_input,
            )
        else:
            result = await self._pipeline.turn(user_input)

        # Auto-save checkpoint after turn
        self._update_checkpoint()
        if self._auto_save:
            self._auto_save._request_save()

        return result

    async def run(self, user_input: UserInput) -> StepResult:
        return await self.turn(user_input)

    def close(self) -> None:
        if self._closed:
            return
        # Final checkpoint
        if self._auto_save:
            self._auto_save.save()
        self._closed = True
        self._session.close()

    def _update_checkpoint(self) -> None:
        if self._auto_save is None:
            return
        self._auto_save.update_checkpoint(
            session_id=self._session.id,
            turn_count=self._session.turn_count,
            messages=list(self._session.context_manager.active_messages),
            spec=self._spec,
            config=asdict(self._cfg),
            store_path=self._session.context_manager.store_path,
        )

    # ── Checkpoint restore ───────────────────────────────────────────────

    @classmethod
    def restore(
        cls,
        spec: HarnessSpec,
        store: CheckpointStore,
        session_id: str,
        llm: LLMProvider | None = None,
        config: HarnessConfig | None = None,
    ) -> HarnessRuntime:
        """Recreate a HarnessRuntime from the latest checkpoint.

        The restored runtime has its session pre-populated with messages
        from the checkpoint.  The caller must still call ``start()`` and
        then resume with ``turn()``.
        """
        cfg = config or HarnessConfig()
        cp = store.load_latest(session_id)
        if cp is None:
            raise ValueError(f"No checkpoint found for session '{session_id}'")

        # Build new runtime (without auto-save to avoid double-write)
        no_save_cfg = HarnessConfig(
            session_budget=cfg.session_budget,
            max_tool_rounds=cfg.max_tool_rounds,
            security_default_decision=cfg.security_default_decision,
            security_require_approval=cfg.security_require_approval,
            pipeline_strategy=cfg.pipeline_strategy,
            enable_auto_save=False,
            metadata=cfg.metadata,
        )
        runtime = cls(spec, llm=llm, config=no_save_cfg, checkpoint_store=store)

        # Rebuild ContextManager with original store path so L2 anchors survive
        from context_manager import (
            ContextManager as _CM,
            LayeredStore as _LS,
            ManagerConfig as _MC,
            StoreConfig as _SC,
        )

        old_cm = runtime._session.context_manager
        old_cm.close()

        new_cm = _CM(
            _MC(total_budget=cfg.session_budget, session_id=session_id),
            store=_LS(_SC(db_path=cp.store_path)) if cp.store_path else None,
        )
        runtime._session._cm = new_cm

        # Restore messages into the new ContextManager
        from context_manager import Message, MessageType as CMMT

        type_map = {e.name: e for e in CMMT}
        for msg_data in cp.messages:
            msg_type = type_map.get(msg_data.get("msg_type", "USER_INPUT"), CMMT.USER_INPUT)
            msg = Message(
                id=msg_data.get("id", ""),
                role=msg_data.get("role", "user"),
                content=msg_data.get("content", ""),
                msg_type=msg_type,
                version=msg_data.get("version", 1),
                metadata=msg_data.get("metadata", {}),
            )
            new_cm.add_message(msg)

        # Re-enable auto-save if needed
        if cfg.enable_auto_save and store:
            runtime._auto_save = AutoSaveManager(
                store, interval=cfg.checkpoint_interval,
            )
            runtime._bus.on(
                PipelineEvent.PIPELINE_TURN,
                runtime._auto_save.create_handler(),
            )
            runtime._bus.on(
                PipelineEvent.SESSION_CLOSED,
                runtime._auto_save.create_handler(),
            )

        logger.info(
            "Restored session %s from checkpoint (%d messages, %d turns, store=%s)",
            session_id, len(cp.messages), cp.turn_count, cp.store_path or "(default)",
        )
        return runtime

    # ── Tool registration ────────────────────────────────────────────────

    def register_tool(
        self,
        definition: ToolDefinition,
        executor: ToolExecutorFn,
    ) -> None:
        self._pipeline.register_tool(definition, executor)

    def register_tools(
        self,
        definitions: list[ToolDefinition],
        executors: list[ToolExecutorFn],
    ) -> None:
        self._pipeline.register_tools(definitions, executors)

    # ── Event subscription ───────────────────────────────────────────────

    def on(self, event: PipelineEvent, handler: EventHandler) -> None:
        self._bus.on(event, handler)

    def off(self, event: PipelineEvent, handler: EventHandler) -> None:
        self._bus.off(event, handler)

    # ── Security policies ────────────────────────────────────────────────

    def add_security_policy(self, policy: Callable) -> None:
        self._security.add_policy(policy)

    @staticmethod
    def _default_executor(**params: Any) -> str:
        return f"[tool executed with params: {params}]"
