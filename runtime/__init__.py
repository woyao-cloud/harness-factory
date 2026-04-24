"""Core Runtime — abstract REPL loop for the HarnessFactory.

Components::

    HarnessRuntime       — main orchestrator (start → turn → close)
    Session              — conversation lifecycle + ContextManager integration
    MessageBus           — event pub/sub for loose coupling
    Pipeline             — REPL loop strategy (interactive / auto / stream)
    LLMProvider          — model abstraction (Anthropic / OpenAI / Mock)
    SecurityGate         — tool-call policy chain

Recovery::

    PipelineRecovery    — retry + circuit breaker for LLM/tool calls
    ErrorClassifier     — categorize errors (retryable / fatal / degrade)

Streaming::

    StreamPipeline      — token-by-token output via StreamEvent
    StreamBuffer        — collect stream events into InferenceResult
    StreamEvent         — typed event (text / tool_call / done)

Checkpoint::

    CheckpointStore     — filesystem save/load
    AutoSaveManager     — periodic save via pipeline events
"""

from .harness import HarnessConfig, HarnessRuntime
from .llm_provider import AnthropicProvider, LLMProvider, MockProvider, OpenAIProvider
from .message_bus import EventHandler, MessageBus
from .pipeline import AutoPipeline, InteractivePipeline, Pipeline, PipelineConfig
from .security import SecurityConfig, SecurityGate, allow_read_only, deny_commands
from .session import Session, SessionConfig

from .recovery import (
    ErrorCategory,
    ErrorClassifier,
    PipelineRecovery,
    RecoveryConfig,
    RetryPolicy,
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    retry,
)

from .streaming import (
    StreamPipeline,
    StreamBuffer,
    StreamEvent,
    StreamType,
    MockStreamProvider,
    StreamProvider,
)

from .checkpoint import (
    Checkpoint,
    CheckpointConfig,
    CheckpointStore,
    AutoSaveManager,
)

from .types import (
    EventPayload,
    HarnessSpec,
    InferenceConfig,
    InferenceResult,
    PipelineEvent,
    PipelinePhase,
    SessionState,
    StepResult,
    ToolCall,
    ToolDefinition,
    ToolParam,
    Usage,
    UserInput,
)

__all__ = [
    # Core
    "HarnessRuntime",
    "HarnessConfig",
    "Session",
    "SessionConfig",
    "MessageBus",
    "EventHandler",
    "Pipeline",
    "InteractivePipeline",
    "AutoPipeline",
    "PipelineConfig",
    "LLMProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "MockProvider",
    "SecurityGate",
    "SecurityConfig",
    "allow_read_only",
    "deny_commands",
    # Recovery
    "PipelineRecovery",
    "RecoveryConfig",
    "ErrorClassifier",
    "ErrorCategory",
    "RetryPolicy",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "CircuitState",
    "retry",
    # Streaming
    "StreamPipeline",
    "StreamBuffer",
    "StreamEvent",
    "StreamType",
    "MockStreamProvider",
    "StreamProvider",
    # Checkpoint
    "Checkpoint",
    "CheckpointConfig",
    "CheckpointStore",
    "AutoSaveManager",
    # Types
    "HarnessSpec",
    "ToolDefinition",
    "ToolParam",
    "ToolCall",
    "UserInput",
    "InferenceConfig",
    "InferenceResult",
    "Usage",
    "StepResult",
    "PipelineEvent",
    "PipelinePhase",
    "SessionState",
    "EventPayload",
]
