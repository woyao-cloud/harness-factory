"""Tests for runtime advanced components: pipeline, streaming, recovery, checkpoint, harness.

These tests depend on MockProvider and other core components to exercise
the full tool loop, streaming, error recovery, checkpoint persistence, and
harness lifecycle.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.llm_provider import MockProvider
from runtime.message_bus import MessageBus
from runtime.pipeline import (
    AutoPipeline,
    InteractivePipeline,
    Pipeline,
    PipelineConfig,
)
from runtime.recovery import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    ErrorCategory,
    ErrorClassifier,
    PipelineRecovery,
    RecoveryConfig,
    RetryPolicy,
    retry,
)
from runtime.security import SecurityGate
from runtime.session import Session, SessionConfig
from runtime.streaming import (
    MockStreamProvider,
    StreamBuffer,
    StreamEvent,
    StreamPipeline,
    StreamType,
)
from runtime.checkpoint import (
    AutoSaveManager,
    Checkpoint,
    CheckpointConfig,
    CheckpointStore,
)
from runtime.types import (
    HarnessSpec,
    InferenceConfig,
    InferenceResult,
    PipelineEvent,
    StepResult,
    ToolCall,
    ToolDefinition,
    Usage,
    UserInput,
)

from runtime.harness import HarnessConfig, HarnessRuntime


# =============================================================================
# Pipeline
# =============================================================================


def _make_session(bus: MessageBus | None = None) -> Session:
    bus = bus or MessageBus()
    return Session(SessionConfig(total_budget=64000), bus)


def _make_pipeline(cls=InteractivePipeline, **kw) -> Pipeline:
    bus = MessageBus()
    session = _make_session(bus)
    llm = MockProvider()
    cfg = PipelineConfig()
    security = SecurityGate()
    return cls(cfg, session, llm, bus, security, **kw)


class TestPipelineBase:
    def test_register_tool(self) -> None:
        pipe = _make_pipeline()
        td = ToolDefinition(name="read", description="Read a file")
        pipe.register_tool(td, lambda **kw: "content")
        assert len(pipe.tool_definitions) == 1
        assert pipe.tool_definitions[0].name == "read"

    def test_register_tools(self) -> None:
        pipe = _make_pipeline()
        tds = [
            ToolDefinition(name="read"),
            ToolDefinition(name="write"),
        ]
        executors = [lambda **kw: "r", lambda **kw: "w"]
        pipe.register_tools(tds, executors)
        assert len(pipe.tool_definitions) == 2

    def test_build_llm_messages(self) -> None:
        bus = MessageBus()
        session = _make_session(bus)
        llm = MockProvider()
        pipe = InteractivePipeline(PipelineConfig(), session, llm, bus, SecurityGate())

        msgs = pipe._build_llm_messages()
        assert isinstance(msgs, list)

    def test_llm_tool_format(self) -> None:
        pipe = _make_pipeline(InteractivePipeline)
        td = ToolDefinition(name="test_tool", description="A test")
        pipe.register_tool(td, lambda **kw: "ok")

        formatted = pipe._llm_tool_format()
        assert len(formatted) == 1
        assert formatted[0]["name"] == "test_tool"


class TestInteractivePipeline:
    def test_turn_returns_step_result(self) -> None:
        pipe = _make_pipeline(InteractivePipeline)
        pipe._session.start()

        result = asyncio_run(pipe.turn(UserInput(text="hello")))
        assert isinstance(result, StepResult)

    def test_turn_records_user_input(self) -> None:
        pipe = _make_pipeline(InteractivePipeline)
        pipe._session.start()
        asyncio_run(pipe.turn(UserInput(text="ping")))
        assert pipe._session.turn_count == 1

    def test_turn_with_tool_execution(self) -> None:
        """When MockProvider returns a tool call, the tool loop should execute it."""
        pipe = _make_pipeline(InteractivePipeline)
        pipe._session.start()

        tool_result_content = "file contents"

        # Register a tool
        td = ToolDefinition(name="read", description="Read a file")
        pipe.register_tool(td, lambda **kw: tool_result_content)

        # Configure MockProvider to first return a tool call, then a text response
        tc = ToolCall(tool_name="read", params={"path": "test.txt"})
        first_response = InferenceResult(
            text="",
            tool_calls=(tc,),
            stop_reason="tool_use",
        )
        pipe._llm.add_response(first_response)

        result = asyncio_run(pipe.turn(UserInput(text="read test.txt")))
        assert result.turn_complete is True
        assert result.text is not None

    def test_turn_with_denied_tool(self) -> None:
        """A tool denied by security should be skipped, not block the loop."""
        pipe = _make_pipeline(InteractivePipeline)
        pipe._session.start()

        # Configure security to deny everything
        pipe._security = SecurityGate()
        from runtime.security import Decision, ApprovalResult

        def deny_all(tc):
            return ApprovalResult(Decision.DENY, "no", "deny_all")
        pipe._security.add_policy(deny_all)

        # Register tool
        td = ToolDefinition(name="bash", description="Run a command")
        pipe.register_tool(td, lambda **kw: "executed")

        # Mock returns a tool call then text
        tc = ToolCall(tool_name="bash", params={"command": "ls"})
        pipe._llm.add_response(InferenceResult(text="", tool_calls=(tc,), stop_reason="tool_use"))

        result = asyncio_run(pipe.turn(UserInput(text="run ls")))
        assert result.turn_complete is True

    def test_turn_complete_flag(self) -> None:
        pipe = _make_pipeline(InteractivePipeline)
        pipe._session.start()

        result = asyncio_run(pipe.turn(UserInput(text="hello")))
        assert result.turn_complete is True

    def test_turn_emits_pipeline_event(self) -> None:
        pipe = _make_pipeline(InteractivePipeline)
        pipe._session.start()

        events: list = []
        pipe._bus.on(PipelineEvent.PIPELINE_TURN, events.append)

        asyncio_run(pipe.turn(UserInput(text="hello")))
        assert len(events) >= 1

    def test_run_calls_turn(self) -> None:
        pipe = _make_pipeline(InteractivePipeline)
        pipe._session.start()

        result = asyncio_run(pipe.run(UserInput(text="run test")))
        assert result.turn_complete is True


class TestAutoPipeline:
    def test_turn_returns_step_result(self) -> None:
        pipe = _make_pipeline(AutoPipeline)
        pipe._session.start()

        result = asyncio_run(pipe.turn(UserInput(text="auto task")))
        assert isinstance(result, StepResult)

    def test_run_calls_turn(self) -> None:
        pipe = _make_pipeline(AutoPipeline)
        pipe._session.start()

        result = asyncio_run(pipe.run(UserInput(text="run auto")))
        assert isinstance(result, StepResult)


# =============================================================================
# Streaming
# =============================================================================


class TestStreamEvent:
    def test_text_event(self) -> None:
        e = StreamEvent.text("Hello", index=1)
        assert e.type == StreamType.TEXT
        assert e.data == "Hello"
        assert e.index == 1

    def test_tool_call_begin(self) -> None:
        e = StreamEvent.tool_call_begin("read", 0)
        assert e.type == StreamType.TOOL_CALL_BEGIN
        assert e.data["name"] == "read"
        assert e.data["index"] == 0

    def test_tool_call_end(self) -> None:
        e = StreamEvent.tool_call_end(0, "read", {"path": "f.txt"})
        assert e.type == StreamType.TOOL_CALL_END
        assert e.data["params"] == {"path": "f.txt"}

    def test_done(self) -> None:
        usage = Usage(input_tokens=10, output_tokens=5)
        e = StreamEvent.done("end_turn", usage)
        assert e.type == StreamType.DONE
        assert e.data["stop_reason"] == "end_turn"
        assert e.data["usage"].input_tokens == 10

    def test_done_without_usage(self) -> None:
        e = StreamEvent.done()
        assert e.type == StreamType.DONE
        assert e.data["usage"] is None

    def test_error(self) -> None:
        e = StreamEvent.error("connection failed")
        assert e.type == StreamType.ERROR
        assert e.data == "connection failed"


class TestStreamBuffer:
    def test_empty_buffer(self) -> None:
        buf = StreamBuffer()
        result = buf.to_result()
        assert result.text == ""
        assert result.tool_calls == ()
        assert result.stop_reason == "end_turn"

    def test_text_accumulation(self) -> None:
        buf = StreamBuffer()
        buf.add(StreamEvent.text("Hello "))
        buf.add(StreamEvent.text("World"))
        assert buf.partial_text == "Hello World"
        assert buf.to_result().text == "Hello World"

    def test_tool_call_lifecycle(self) -> None:
        buf = StreamBuffer()
        buf.add(StreamEvent.tool_call_begin("read", 0, {}))
        buf.add(StreamEvent.tool_call_end(0, "read", {"path": "f.txt"}))
        result = buf.to_result()
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "read"
        assert result.tool_calls[0].params == {"path": "f.txt"}

    def test_done_sets_usage(self) -> None:
        buf = StreamBuffer()
        usage = Usage(input_tokens=100, output_tokens=20)
        buf.add(StreamEvent.done("end_turn", usage))
        result = buf.to_result()
        assert result.usage.input_tokens == 100
        assert result.usage.output_tokens == 20

    def test_error_recorded(self) -> None:
        buf = StreamBuffer()
        buf.add(StreamEvent.error("timeout"))
        assert buf._error == "timeout"

    def test_multiple_tool_calls(self) -> None:
        buf = StreamBuffer()
        buf.add(StreamEvent.tool_call_begin("read", 0, {}))
        buf.add(StreamEvent.tool_call_end(0, "read", {"path": "a.txt"}))
        buf.add(StreamEvent.tool_call_begin("write", 1, {}))
        buf.add(StreamEvent.tool_call_end(1, "write", {"path": "b.txt", "content": "hi"}))
        result = buf.to_result()
        assert len(result.tool_calls) == 2

    def test_partial_text_property(self) -> None:
        buf = StreamBuffer()
        assert buf.partial_text == ""
        buf.add(StreamEvent.text("partial"))
        assert buf.partial_text == "partial"


class TestMockStreamProvider:
    def test_stream_complete(self) -> None:
        provider = MockStreamProvider()
        events: list[StreamEvent] = []

        async def collect():
            async for e in provider.stream_complete([], [], InferenceConfig()):
                events.append(e)

        asyncio_run(collect())
        assert len(events) >= 1
        assert events[-1].type == StreamType.DONE

    def test_complete_non_streaming(self) -> None:
        """The non-streaming fallback should produce a valid InferenceResult."""
        provider = MockStreamProvider()
        result = asyncio_run(
            provider.complete([], [], InferenceConfig())
        )
        assert isinstance(result, InferenceResult)
        assert "[mock stream]" in result.text

    def test_custom_sequence(self) -> None:
        provider = MockStreamProvider()
        custom = [
            StreamEvent.text("custom "),
            StreamEvent.text("sequence"),
            StreamEvent.done("end_turn", Usage(input_tokens=5, output_tokens=3)),
        ]
        provider.add_sequence(custom)

        events: list[StreamEvent] = []

        async def collect():
            async for e in provider.stream_complete([], [], InferenceConfig()):
                events.append(e)

        asyncio_run(collect())
        texts = [e.data for e in events if e.type == StreamType.TEXT]
        assert "".join(texts) == "custom sequence"
        assert events[-1].data["usage"].input_tokens == 5


class TestStreamPipeline:
    def test_turn_returns_step_result(self) -> None:
        bus = MessageBus()
        session = _make_session(bus)
        llm = MockProvider()
        pipe = StreamPipeline(PipelineConfig(), session, llm, bus, SecurityGate())
        session.start()

        result = asyncio_run(pipe.turn(UserInput(text="hello")))
        assert isinstance(result, StepResult)

    def test_streaming_with_mock_provider(self) -> None:
        bus = MessageBus()
        session = _make_session(bus)
        llm = MockStreamProvider()
        pipe = StreamPipeline(PipelineConfig(), session, llm, bus, SecurityGate())
        session.start()

        result = asyncio_run(pipe.turn(UserInput(text="stream test")))
        assert isinstance(result, StepResult)

    def test_stream_emits_events(self) -> None:
        bus = MessageBus()
        session = _make_session(bus)
        llm = MockStreamProvider()
        pipe = StreamPipeline(PipelineConfig(), session, llm, bus, SecurityGate())
        session.start()

        output_events: list = []
        bus.on(PipelineEvent.OUTPUT_TEXT, output_events.append)

        asyncio_run(pipe.turn(UserInput(text="emit test")))
        assert len(output_events) >= 1


# =============================================================================
# Recovery
# =============================================================================


class TestErrorClassifier:
    def test_connection_error_is_retryable(self) -> None:
        cls = ErrorClassifier()
        cat, reason = cls.classify(ConnectionError("connection refused"))
        assert cat is ErrorCategory.RETRYABLE

    def test_timeout_is_retryable(self) -> None:
        cls = ErrorClassifier()
        cat, _ = cls.classify(TimeoutError("timed out"))
        assert cat is ErrorCategory.RETRYABLE

    def test_custom_register(self) -> None:
        cls = ErrorClassifier()
        cls.register(ValueError, ErrorCategory.DEGRADE)
        cat, reason = cls.classify(ValueError("bad value"))
        assert cat is ErrorCategory.DEGRADE

    def test_heuristic_rate_limit(self) -> None:
        cls = ErrorClassifier()
        cat, reason = cls.classify(RuntimeError("rate limit exceeded"))
        assert cat is ErrorCategory.RETRYABLE
        assert reason == "rate_limit"

    def test_heuristic_auth(self) -> None:
        cls = ErrorClassifier()
        cat, reason = cls.classify(PermissionError("unauthorized"))
        assert cat is ErrorCategory.FATAL
        assert reason == "auth_error"

    def test_heuristic_not_found(self) -> None:
        cls = ErrorClassifier()
        cat, reason = cls.classify(FileNotFoundError("not found"))
        assert cat is ErrorCategory.DEGRADE

    def test_unknown_default(self) -> None:
        cls = ErrorClassifier()
        cat, reason = cls.classify(RuntimeError("something weird"))
        assert cat is ErrorCategory.UNKNOWN

    def test_custom_predicate(self) -> None:
        cls = ErrorClassifier()
        cls.register_predicate(
            lambda e: ErrorCategory.RETRYABLE if "retry_me" in str(e) else None,
            name="custom_retry",
        )
        cat, reason = cls.classify(RuntimeError("please retry_me"))
        assert cat is ErrorCategory.RETRYABLE
        assert reason == "custom_retry"


class TestRetryPolicy:
    def test_delay_increases(self) -> None:
        policy = RetryPolicy(base_delay=1.0, multiplier=2.0, jitter=0)
        d0 = policy.delay(0)
        d1 = policy.delay(1)
        d2 = policy.delay(2)
        assert d0 == 1.0
        assert d1 == 2.0
        assert d2 == 4.0

    def test_delay_capped(self) -> None:
        policy = RetryPolicy(base_delay=10.0, max_delay=25.0, multiplier=4.0, jitter=0)
        d = policy.delay(2)
        assert d == 25.0  # 10 * 4^2 = 160, capped to 25

    def test_delay_with_jitter(self) -> None:
        policy = RetryPolicy(base_delay=1.0, multiplier=2.0, jitter=0.1)
        values = {policy.delay(1) for _ in range(50)}
        # With ±10% jitter on 2.0, range should be roughly [1.8, 2.2]
        assert any(v != 2.0 for v in values), "jitter should produce variance"


class TestRetryFunction:
    def test_retry_success_first_try(self) -> None:
        calls = 0

        async def fn():
            nonlocal calls
            calls += 1
            return "ok"

        result = asyncio_run(retry(fn, RetryPolicy(max_retries=3)))
        assert result == "ok"
        assert calls == 1

    def test_retry_eventually_succeeds(self) -> None:
        calls = 0

        async def fn():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ConnectionError("transient")
            return "recovered"

        result = asyncio_run(retry(fn, RetryPolicy(max_retries=3, base_delay=0.01, jitter=0)))
        assert result == "recovered"
        assert calls == 3

    def test_retry_exhausted(self) -> None:
        async def fn():
            raise ConnectionError("persistent")

        with pytest.raises(ConnectionError):
            asyncio_run(retry(fn, RetryPolicy(max_retries=2, base_delay=0.01, jitter=0)))

    def test_non_retryable_raises_immediately(self) -> None:
        async def fn():
            raise ValueError("fatal")

        with pytest.raises(ValueError):
            asyncio_run(
                retry(
                    fn,
                    RetryPolicy(max_retries=3, base_delay=0.01),
                    classifier=ErrorClassifier(),
                )
            )


class TestCircuitBreaker:
    def test_initial_state(self) -> None:
        cb = CircuitBreaker("test")
        assert cb.state is CircuitState.CLOSED

    def test_open_after_threshold(self) -> None:
        cb = CircuitBreaker("test", threshold=3, reset_timeout=60)
        for _ in range(3):
            cb.on_failure()
        assert cb.state is CircuitState.OPEN

    def test_on_success_in_half_open(self) -> None:
        cb = CircuitBreaker("test", threshold=1, reset_timeout=60, half_open_max=1)
        cb.on_failure()  # → OPEN
        # Force to HALF_OPEN via state property (simulates timeout)
        cb._open_until = 0  # past timeout
        assert cb.state is CircuitState.HALF_OPEN

        cb.on_success()  # should close
        assert cb.state is CircuitState.CLOSED

    def test_stats(self) -> None:
        cb = CircuitBreaker("test", threshold=3)
        cb.on_failure()
        stats = cb.stats
        assert stats.failure_count == 1
        assert stats.state is CircuitState.CLOSED

    def test_reset(self) -> None:
        cb = CircuitBreaker("test", threshold=1)
        cb.on_failure()
        assert cb.state is CircuitState.OPEN
        cb.reset()
        assert cb.state is CircuitState.CLOSED
        assert cb.stats.failure_count == 0

    def test_protect_context_manager_success(self) -> None:
        cb = CircuitBreaker("test")

        async def run():
            async with cb.protect():
                return "ok"

        result = asyncio_run(run())
        assert result == "ok"

    def test_protect_context_manager_open(self) -> None:
        cb = CircuitBreaker("test", threshold=1, reset_timeout=60)
        cb.on_failure()  # → OPEN

        async def run():
            with pytest.raises(CircuitBreakerOpenError):
                async with cb.protect():
                    pass  # pragma: no cover

        asyncio_run(run())

    def test_protect_with_fallback(self) -> None:
        cb = CircuitBreaker("test", threshold=1, reset_timeout=60)
        cb.on_failure()  # → OPEN

        async def run():
            async with cb.protect(fallback=lambda: "fallback") as result:
                return result

        result = asyncio_run(run())
        assert result == "fallback"

    def test_on_failure_in_half_open_reopens(self) -> None:
        cb = CircuitBreaker("test", threshold=1, reset_timeout=60, half_open_max=1)
        cb.on_failure()  # CLOSED → OPEN
        cb._open_until = 0  # force HALF_OPEN
        _ = cb.state  # triggers transition to HALF_OPEN

        cb.on_failure()  # HALF_OPEN → OPEN
        assert cb.state is CircuitState.OPEN

    def test_success_in_closed_reduces_failure_count(self) -> None:
        cb = CircuitBreaker("test", threshold=5)
        cb._failure_count = 3
        cb.on_success()
        assert cb._failure_count == 2  # gradual recovery

    def test_protect_raises_on_exception(self) -> None:
        cb = CircuitBreaker("test")

        async def run():
            async with cb.protect():
                raise ValueError("boom")

        with pytest.raises(ValueError):
            asyncio_run(run())

        # Failure should be recorded
        assert cb.stats.failure_count == 1


class TestPipelineRecovery:
    async def _success_fn(self, **kw):
        return "ok"

    async def _failing_fn(self, **kw):
        raise ConnectionError("network issue")

    def test_call_with_recovery_success(self) -> None:
        recovery = PipelineRecovery(RecoveryConfig(retry_policy=RetryPolicy(max_retries=2, base_delay=0.01)))
        result = asyncio_run(recovery.call_with_recovery("test", self._success_fn))
        assert result == "ok"

    def test_call_with_recovery_failure(self) -> None:
        recovery = PipelineRecovery(RecoveryConfig(retry_policy=RetryPolicy(max_retries=1, base_delay=0.01)))
        with pytest.raises(ConnectionError):
            asyncio_run(recovery.call_with_recovery("test", self._failing_fn))

    def test_consecutive_failures_threshold(self) -> None:
        recovery = PipelineRecovery(
            RecoveryConfig(
                retry_policy=RetryPolicy(max_retries=0, base_delay=0.01),
                max_consecutive_failures=2,
            )
        )

        with pytest.raises(RuntimeError, match="Too many consecutive failures"):
            for _ in range(3):
                try:
                    asyncio_run(recovery.call_with_recovery("test", self._failing_fn))
                except (ConnectionError, RuntimeError):
                    if recovery.consecutive_failures >= 2:
                        raise

    def test_get_breaker(self) -> None:
        recovery = PipelineRecovery()
        b1 = recovery.get_breaker("llm")
        b2 = recovery.get_breaker("llm")
        assert b1 is b2

    def test_classify(self) -> None:
        recovery = PipelineRecovery()
        cat, reason = recovery.classify(TimeoutError())
        assert cat is ErrorCategory.RETRYABLE

    def test_circuit_breaker_prevents_calls_when_open(self) -> None:
        recovery = PipelineRecovery(
            RecoveryConfig(retry_policy=RetryPolicy(max_retries=0, base_delay=0.01))
        )

        # Open the circuit breaker
        for _ in range(5):
            try:
                asyncio_run(recovery.call_with_recovery("test", self._failing_fn))
            except (ConnectionError, RuntimeError):
                pass

        # Next call should raise CircuitBreakerOpenError
        with pytest.raises(CircuitBreakerOpenError):
            asyncio_run(recovery.call_with_recovery("test", self._failing_fn))


# =============================================================================
# Checkpoint
# =============================================================================


class TestCheckpoint:
    def test_defaults(self) -> None:
        cp = Checkpoint(session_id="s1")
        assert cp.version == 1
        assert cp.turn_count == 0
        assert cp.messages == ()

    def test_full(self) -> None:
        cp = Checkpoint(
            session_id="s1",
            turn_count=5,
            messages=({"role": "user", "content": "hi"},),
            store_path="/tmp/store.db",
        )
        assert cp.turn_count == 5
        assert cp.store_path == "/tmp/store.db"
        assert len(cp.messages) == 1


class TestCheckpointStore:
    def test_save_and_load(self, tmp_path: Path) -> None:
        store = CheckpointStore(CheckpointConfig(save_dir=str(tmp_path / "cp")))
        cp = Checkpoint(
            session_id="test_session",
            turn_count=3,
            messages=({"role": "user", "content": "hello"},),
        )
        path = store.save(cp)
        assert path.exists()

        loaded = store.load_latest("test_session")
        assert loaded is not None
        assert loaded.turn_count == 3
        assert len(loaded.messages) == 1

    def test_load_nonexistent(self, tmp_path: Path) -> None:
        store = CheckpointStore(CheckpointConfig(save_dir=str(tmp_path / "cp")))
        loaded = store.load_latest("no_such_session")
        assert loaded is None

    def test_load_with_meta(self, tmp_path: Path) -> None:
        store = CheckpointStore(CheckpointConfig(save_dir=str(tmp_path / "cp")))

        # Save two checkpoints, verify we get the latest
        cp1 = Checkpoint(session_id="s1", turn_count=1)
        store.save(cp1)
        cp2 = Checkpoint(session_id="s1", turn_count=2)
        store.save(cp2)

        loaded = store.load_latest("s1")
        assert loaded is not None
        assert loaded.turn_count == 2

    def test_list_checkpoints(self, tmp_path: Path) -> None:
        store = CheckpointStore(CheckpointConfig(save_dir=str(tmp_path / "cp")))
        store.save(Checkpoint(session_id="s1", turn_count=1))
        store.save(Checkpoint(session_id="s1", turn_count=2))

        entries = store.list_checkpoints("s1")
        assert len(entries) >= 1

    def test_delete_session(self, tmp_path: Path) -> None:
        store = CheckpointStore(CheckpointConfig(save_dir=str(tmp_path / "cp")))
        store.save(Checkpoint(session_id="to_delete"))
        assert store.load_latest("to_delete") is not None

        store.delete_session("to_delete")
        assert store.load_latest("to_delete") is None

    def test_prune_excess(self, tmp_path: Path) -> None:
        """Should keep at most max_checkpoints_per_session."""
        store = CheckpointStore(
            CheckpointConfig(save_dir=str(tmp_path / "cp"), max_checkpoints_per_session=3)
        )
        for i in range(5):
            store.save(Checkpoint(session_id="s1", turn_count=i))

        entries = store.list_checkpoints("s1")
        assert len(entries) <= 3


class TestAutoSaveManager:
    def test_update_and_save(self, tmp_path: Path) -> None:
        store = CheckpointStore(CheckpointConfig(save_dir=str(tmp_path / "cp")))
        mgr = AutoSaveManager(store, interval=0)

        mgr.update_checkpoint(
            session_id="s1",
            turn_count=5,
            messages=[],
        )
        mgr.save()

        loaded = store.load_latest("s1")
        assert loaded is not None
        assert loaded.turn_count == 5

    def test_handler_fires_on_event(self, tmp_path: Path) -> None:
        store = CheckpointStore(CheckpointConfig(save_dir=str(tmp_path / "cp")))
        mgr = AutoSaveManager(store, interval=0)
        mgr.update_checkpoint(session_id="s1", turn_count=1, messages=[])

        handler = mgr.create_handler()
        from runtime.types import EventPayload
        handler(EventPayload(event=PipelineEvent.PIPELINE_TURN))

        loaded = store.load_latest("s1")
        assert loaded is not None

    def test_handler_no_save_on_interval(self, tmp_path: Path) -> None:
        """Should not save if interval hasn't elapsed."""
        store = CheckpointStore(CheckpointConfig(save_dir=str(tmp_path / "cp")))
        mgr = AutoSaveManager(store, interval=9999)  # very long interval
        mgr.update_checkpoint(session_id="s1", turn_count=1, messages=[])

        import time
        mgr._last_save = time.time()  # pretend we just saved
        handler = mgr.create_handler()
        from runtime.types import EventPayload
        handler(EventPayload(event=PipelineEvent.PIPELINE_TURN))

        # Since interval is long, it should NOT save
        loaded = store.load_latest("s1")
        assert loaded is None, "Should not save before interval elapses"

    def test_save_without_update_does_nothing(self, tmp_path: Path) -> None:
        store = CheckpointStore(CheckpointConfig(save_dir=str(tmp_path / "cp")))
        mgr = AutoSaveManager(store, interval=0)
        mgr.save()  # should not raise even with no checkpoint set


# =============================================================================
# Harness
# =============================================================================


class TestHarnessRuntime:
    def test_initialization(self) -> None:
        spec = HarnessSpec(name="test-harness", description="Test harness")
        harness = HarnessRuntime(spec)
        assert harness.spec.name == "test-harness"
        assert harness.session.state.name == "CREATED"

    def test_start(self) -> None:
        spec = HarnessSpec(name="test")
        harness = HarnessRuntime(spec)
        harness.start()
        assert harness.session.state.name == "ACTIVE"

    def test_turn(self) -> None:
        spec = HarnessSpec(name="test")
        harness = HarnessRuntime(spec)
        harness.start()

        result = asyncio_run(harness.turn(UserInput(text="hello")))
        assert isinstance(result, StepResult)

    def test_run(self) -> None:
        spec = HarnessSpec(name="test")
        harness = HarnessRuntime(spec)
        harness.start()

        result = asyncio_run(harness.run(UserInput(text="hello")))
        assert isinstance(result, StepResult)

    def test_close(self) -> None:
        spec = HarnessSpec(name="test")
        harness = HarnessRuntime(spec)
        harness.start()
        harness.close()
        assert harness.session.state.name == "CLOSED"

    def test_close_idempotent(self) -> None:
        spec = HarnessSpec(name="test")
        harness = HarnessRuntime(spec)
        harness.close()
        harness.close()  # should not raise

    def test_cannot_restart_closed(self) -> None:
        spec = HarnessSpec(name="test")
        harness = HarnessRuntime(spec)
        harness.close()
        with pytest.raises(RuntimeError):
            harness.start()

    def test_register_tool(self) -> None:
        spec = HarnessSpec(name="test")
        harness = HarnessRuntime(spec)

        td = ToolDefinition(name="my_tool", description="A custom tool")
        harness.register_tool(td, lambda **kw: "custom result")

        assert len(harness.pipeline.tool_definitions) == 1

    def test_register_tools(self) -> None:
        spec = HarnessSpec(name="test")
        harness = HarnessRuntime(spec)

        tds = [
            ToolDefinition(name="tool_a"),
            ToolDefinition(name="tool_b"),
        ]
        harness.register_tools(tds, [lambda **kw: "a", lambda **kw: "b"])

        assert len(harness.pipeline.tool_definitions) == 2

    def test_event_subscription(self) -> None:
        spec = HarnessSpec(name="test")
        harness = HarnessRuntime(spec)

        received: list = []
        harness.on(PipelineEvent.USER_INPUT, received.append)

        harness.start()
        asyncio_run(harness.turn(UserInput(text="event test")))

        assert len(received) >= 1

    def test_add_security_policy(self) -> None:
        spec = HarnessSpec(name="test")
        harness = HarnessRuntime(spec)

        from runtime.security import Decision, ApprovalResult
        policy_called = False

        def custom_policy(tc):
            nonlocal policy_called
            policy_called = True
            return ApprovalResult(Decision.ALLOW, "custom", "custom")

        harness.add_security_policy(custom_policy)

        tc = ToolCall(tool_name="read")
        harness.security.approve(tc)
        assert policy_called is True

    def test_with_recovery(self) -> None:
        recovery = PipelineRecovery()
        spec = HarnessSpec(name="test")
        harness = HarnessRuntime(spec, recovery=recovery)
        assert harness.recovery is recovery

    def test_with_checkpoint_store(self) -> None:
        from runtime.checkpoint import CheckpointConfig, CheckpointStore

        config = HarnessConfig(enable_auto_save=False)
        store = CheckpointStore(CheckpointConfig(save_dir="checkpoints_test"))
        spec = HarnessSpec(name="test")
        harness = HarnessRuntime(spec, config=config, checkpoint_store=store)
        assert harness.checkpoint_store is store

    def test_properties(self) -> None:
        spec = HarnessSpec(name="test")
        harness = HarnessRuntime(spec)
        assert harness.bus is not None
        assert harness.security is not None
        assert harness.pipeline is not None

    def test_turn_after_close_raises(self) -> None:
        spec = HarnessSpec(name="test")
        harness = HarnessRuntime(spec)
        harness.close()

        with pytest.raises(RuntimeError, match="closed"):
            asyncio_run(harness.turn(UserInput(text="nope")))

    def test_config_defaults(self) -> None:
        spec = HarnessSpec(name="test")
        harness = HarnessRuntime(spec)
        cfg = harness._cfg
        assert cfg.session_budget == 64000
        assert cfg.max_tool_rounds == 25

    def test_custom_config(self) -> None:
        spec = HarnessSpec(name="test")
        cfg = HarnessConfig(session_budget=1000, max_tool_rounds=5)
        harness = HarnessRuntime(spec, config=cfg)
        assert harness._cfg.session_budget == 1000
        assert harness._cfg.max_tool_rounds == 5


# =============================================================================
# Helpers
# =============================================================================


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)
