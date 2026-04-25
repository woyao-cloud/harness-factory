"""Tests for runtime core components: types, message_bus, session, security, llm_provider.

These tests have minimal dependencies — they test isolated components with
no need for the full pipeline or context manager set up.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from runtime.types import (
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

from runtime.message_bus import MessageBus

from runtime.session import Session, SessionConfig

from runtime.security import (
    ApprovalResult,
    Decision,
    SecurityConfig,
    SecurityGate,
    allow_path_prefix,
    allow_read_only,
    deny_commands,
)

from runtime.llm_provider import MockProvider


# =============================================================================
# types
# =============================================================================


class TestEnums:
    def test_session_state_values(self) -> None:
        assert SessionState.CREATED.value == 1
        assert SessionState.ACTIVE.value == 2
        assert SessionState.CLOSED.value == 4

    def test_pipeline_phase_order(self) -> None:
        assert PipelinePhase.IDLE.value < PipelinePhase.INFERENCE.value
        assert PipelinePhase.ERROR.value > PipelinePhase.OUTPUT.value

    def test_pipeline_event_string_values(self) -> None:
        assert PipelineEvent.SESSION_STARTED == "session.started"
        assert PipelineEvent.USER_INPUT == "user.input"
        assert PipelineEvent.ERROR == "error"


class TestToolParam:
    def test_defaults(self) -> None:
        p = ToolParam(name="p1")
        assert p.name == "p1"
        assert p.type == "string"
        assert p.description == ""
        assert p.required is False

    def test_full(self) -> None:
        p = ToolParam(name="count", type="integer", description="how many", required=True)
        assert p.name == "count"
        assert p.type == "integer"
        assert p.required is True


class TestToolDefinition:
    def test_minimal(self) -> None:
        d = ToolDefinition(name="my_tool")
        assert d.name == "my_tool"
        assert d.description == ""
        assert d.parameters == ()

    def test_to_anthropic_tool(self) -> None:
        d = ToolDefinition(
            name="search",
            description="Search the web",
            parameters=(
                ToolParam(name="query", description="search term", required=True),
                ToolParam(name="limit", type="integer", description="max results", required=False),
            ),
        )
        result = d.to_anthropic_tool()
        assert result["name"] == "search"
        assert "query" in result["input_schema"]["properties"]
        assert "limit" in result["input_schema"]["properties"]
        assert result["input_schema"]["required"] == ["query"]

    def test_to_openai_tool(self) -> None:
        d = ToolDefinition(
            name="search",
            description="Search the web",
            parameters=(ToolParam(name="query", description="search term"),),
        )
        result = d.to_openai_tool()
        assert result["type"] == "function"
        assert result["function"]["name"] == "search"
        assert "query" in result["function"]["parameters"]["properties"]


class TestInferenceConfig:
    def test_defaults(self) -> None:
        c = InferenceConfig()
        assert c.model == "deepseek-v4-flash:cloud"
        assert c.max_tokens == 4096
        assert c.temperature == 0.7
        assert c.thinking is False


class TestInferenceResult:
    def test_empty(self) -> None:
        r = InferenceResult()
        assert r.text == ""
        assert r.tool_calls == ()
        assert r.stop_reason == "end_turn"

    def test_with_tool_calls(self) -> None:
        calls = (ToolCall(tool_name="read", params={"path": "x.txt"}),)
        r = InferenceResult(text="calling tool", tool_calls=calls)
        assert r.text == "calling tool"
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0].tool_name == "read"


class TestToolCall:
    def test_defaults(self) -> None:
        tc = ToolCall()
        assert tc.status == "pending"
        assert tc.result is None
        assert tc.error is None
        assert tc.id.startswith("call_")

    def test_full(self) -> None:
        tc = ToolCall(
            id="call_abc",
            tool_name="write",
            params={"path": "f.txt", "content": "hi"},
            status="completed",
            result="ok",
        )
        assert tc.id == "call_abc"
        assert tc.tool_name == "write"
        assert tc.result == "ok"


class TestUserInput:
    def test_defaults(self) -> None:
        ui = UserInput(text="hello")
        assert ui.text == "hello"
        assert ui.attachments == ()
        assert isinstance(ui.timestamp, datetime)

    def test_with_attachments(self) -> None:
        ui = UserInput(text="analyze", attachments=("f1.csv", "f2.csv"))
        assert len(ui.attachments) == 2


class TestHarnessSpec:
    def test_minimal(self) -> None:
        spec = HarnessSpec(name="test")
        assert spec.name == "test"
        assert spec.version == "0.1.0"
        assert spec.resolve_tool("none") is None

    def test_resolve_tool(self) -> None:
        td = ToolDefinition(name="read")
        spec = HarnessSpec(name="test", tools=(td,))
        assert spec.resolve_tool("read") is td
        assert spec.resolve_tool("write") is None


class TestEventPayload:
    def test_defaults(self) -> None:
        ep = EventPayload(event=PipelineEvent.ERROR)
        assert ep.phase == PipelinePhase.IDLE
        assert ep.data == {}
        assert isinstance(ep.timestamp, datetime)

    def test_with_data(self) -> None:
        ep = EventPayload(
            event=PipelineEvent.TOOL_CALL_DETECTED,
            phase=PipelinePhase.BEFORE_TOOL,
            data={"tool": "read", "params": {}},
        )
        assert ep.event == PipelineEvent.TOOL_CALL_DETECTED
        assert ep.data["tool"] == "read"


class TestStepResult:
    def test_defaults(self) -> None:
        sr = StepResult()
        assert sr.text == ""
        assert sr.tool_calls == ()
        assert sr.turn_complete is False
        assert sr.error is None

    def test_full(self) -> None:
        tc = (ToolCall(tool_name="glob"),)
        sr = StepResult(text="done", tool_calls=tc, turn_complete=True, session_complete=True)
        assert sr.text == "done"
        assert len(sr.tool_calls) == 1


class TestUsage:
    def test_defaults(self) -> None:
        u = Usage()
        assert u.input_tokens == 0
        assert u.output_tokens == 0

    def test_sum(self) -> None:
        u = Usage(input_tokens=100, output_tokens=50)
        assert u.input_tokens == 100
        assert u.output_tokens == 50


# =============================================================================
# MessageBus
# =============================================================================


class TestMessageBus:
    def test_emit_and_subscribe(self) -> None:
        bus = MessageBus()
        received: list[EventPayload] = []

        bus.on(PipelineEvent.USER_INPUT, received.append)
        bus.emit(PipelineEvent.USER_INPUT, phase=PipelinePhase.PARSING_INPUT, text="hello")

        assert len(received) == 1
        assert received[0].data["text"] == "hello"

    def test_unsubscribe(self) -> None:
        bus = MessageBus()
        received: list[EventPayload] = []

        handler = received.append
        bus.on(PipelineEvent.ERROR, handler)
        bus.off(PipelineEvent.ERROR, handler)
        bus.emit(PipelineEvent.ERROR)

        assert len(received) == 0

    def test_history(self) -> None:
        bus = MessageBus()
        bus.emit(PipelineEvent.SESSION_STARTED)
        bus.emit(PipelineEvent.USER_INPUT, text="hi")

        assert len(bus.history) == 2
        assert bus.history[0].event == PipelineEvent.SESSION_STARTED

    def test_clear_history(self) -> None:
        bus = MessageBus()
        bus.emit(PipelineEvent.ERROR)
        bus.clear_history()
        assert len(bus.history) == 0

    def test_last_event(self) -> None:
        bus = MessageBus()
        assert bus.last_event() is None

        bus.emit(PipelineEvent.SESSION_STARTED)
        bus.emit(PipelineEvent.USER_INPUT, text="hi")
        bus.emit(PipelineEvent.USER_INPUT, text="bye")

        last = bus.last_event(PipelineEvent.USER_INPUT)
        assert last is not None
        assert last.data["text"] == "bye"

    def test_handler_error_isolation(self) -> None:
        """A failing handler should not prevent other handlers from running."""
        bus = MessageBus()
        results: list[str] = []

        def failing(p: EventPayload) -> None:
            raise ValueError("boom")

        def working(p: EventPayload) -> None:
            results.append("ok")

        bus.on(PipelineEvent.ERROR, failing)
        bus.on(PipelineEvent.ERROR, working)

        # Suppress logging noise for the expected error
        logging.getLogger("runtime.message_bus").setLevel(logging.CRITICAL)
        bus.emit(PipelineEvent.ERROR)

        assert results == ["ok"]

    def test_last_event_no_filter(self) -> None:
        bus = MessageBus()
        bus.emit(PipelineEvent.SESSION_STARTED)
        bus.emit(PipelineEvent.USER_INPUT)
        last = bus.last_event()
        assert last is not None
        assert last.event == PipelineEvent.USER_INPUT


# =============================================================================
# Session
# =============================================================================


class TestSession:
    def test_initial_state(self) -> None:
        bus = MessageBus()
        session = Session(SessionConfig(total_budget=1000), bus)
        assert session.state == SessionState.CREATED
        assert session.turn_count == 0

    def test_start(self) -> None:
        bus = MessageBus()
        session = Session(SessionConfig(total_budget=1000), bus)
        session.start()
        assert session.state == SessionState.ACTIVE

    def test_start_twice_raises(self) -> None:
        bus = MessageBus()
        session = Session(SessionConfig(total_budget=1000), bus)
        session.start()
        with pytest.raises(RuntimeError, match="Cannot start"):
            session.start()

    def test_pause_and_resume(self) -> None:
        bus = MessageBus()
        session = Session(SessionConfig(total_budget=1000), bus)
        session.start()
        session.pause()
        assert session.state == SessionState.PAUSED
        session.resume()
        assert session.state == SessionState.ACTIVE

    def test_pause_from_created_raises(self) -> None:
        bus = MessageBus()
        session = Session(SessionConfig(total_budget=1000), bus)
        with pytest.raises(RuntimeError, match="Cannot pause"):
            session.pause()

    def test_close(self) -> None:
        bus = MessageBus()
        session = Session(SessionConfig(total_budget=1000), bus)
        session.start()
        session.close()
        assert session.state == SessionState.CLOSED

    def test_close_idempotent(self) -> None:
        bus = MessageBus()
        session = Session(SessionConfig(total_budget=1000), bus)
        session.close()
        session.close()  # should not raise
        assert session.state == SessionState.CLOSED

    def test_turn_count_increments(self) -> None:
        bus = MessageBus()
        session = Session(SessionConfig(total_budget=1000), bus)
        session.start()
        session.record_user_input(UserInput(text="first"))
        assert session.turn_count == 1
        session.record_user_input(UserInput(text="second"))
        assert session.turn_count == 2

    def test_record_user_input_emits_event(self) -> None:
        bus = MessageBus()
        session = Session(SessionConfig(total_budget=1000), bus)
        session.start()
        events: list[EventPayload] = []
        bus.on(PipelineEvent.USER_INPUT, events.append)

        session.record_user_input(UserInput(text="hello"))

        assert len(events) == 1
        assert events[0].data["text"] == "hello"

    def test_session_id_generated(self) -> None:
        bus = MessageBus()
        s1 = Session(SessionConfig(total_budget=1000, session_id="sess_a"), bus)
        s2 = Session(SessionConfig(total_budget=1000, session_id="sess_b"), bus)
        assert s1.id == "sess_a"
        assert s2.id == "sess_b"

    def test_auto_generated_id_format(self) -> None:
        bus = MessageBus()
        s = Session(SessionConfig(total_budget=1000), bus)
        assert s.id.startswith("sess_")
        assert len(s.id) > len("sess_")

    def test_context_manager_property(self) -> None:
        bus = MessageBus()
        session = Session(SessionConfig(total_budget=1000), bus)
        cm = session.context_manager
        assert cm is not None
        # Should be the same instance
        assert session.context_manager is cm

    def test_record_assistant_text(self) -> None:
        bus = MessageBus()
        session = Session(SessionConfig(total_budget=1000), bus)
        session.start()
        # Should not raise
        session.record_assistant_text("Hello, I'm Claude.")

    def test_record_tool_result(self) -> None:
        bus = MessageBus()
        session = Session(SessionConfig(total_budget=1000), bus)
        session.start()
        session.record_tool_result("read", "file content")
        # With error
        session.record_tool_result("write", "failed", error="permission denied")

    def test_start_emits_event(self) -> None:
        bus = MessageBus()
        session = Session(SessionConfig(total_budget=1000), bus)
        events: list[EventPayload] = []
        bus.on(PipelineEvent.SESSION_STARTED, events.append)

        session.start()
        assert len(events) == 1

    def test_close_emits_event(self) -> None:
        bus = MessageBus()
        session = Session(SessionConfig(total_budget=1000), bus)
        session.start()

        events: list[EventPayload] = []
        bus.on(PipelineEvent.SESSION_CLOSED, events.append)

        session.close()
        assert len(events) == 1
        assert events[0].data["turn_count"] == 0

    def test_session_with_custom_id(self) -> None:
        bus = MessageBus()
        session = Session(SessionConfig(session_id="my-session", total_budget=1000), bus)
        assert session.id == "my-session"


# =============================================================================
# Security
# =============================================================================


class TestDecision:
    def test_enum_values(self) -> None:
        assert Decision.ALLOW.name == "ALLOW"
        assert Decision.DENY.name == "DENY"
        assert Decision.ASK.name == "ASK"


class TestApprovalResult:
    def test_defaults(self) -> None:
        ar = ApprovalResult()
        assert ar.decision is Decision.DENY
        assert ar.reason == ""

    def test_full(self) -> None:
        ar = ApprovalResult(decision=Decision.ALLOW, reason="safe", policy_name="read_only")
        assert ar.decision is Decision.ALLOW
        assert ar.policy_name == "read_only"


class TestAllowReadOnly:
    def test_allow_read_tools(self) -> None:
        for name in ("read", "list", "grep", "search"):
            tc = ToolCall(tool_name=name)
            result = allow_read_only(tc)
            assert result is not None
            assert result.decision is Decision.ALLOW

    def test_deny_mutate_tools(self) -> None:
        for name in ("write", "edit", "delete", "bash"):
            tc = ToolCall(tool_name=name)
            result = allow_read_only(tc)
            assert result is not None
            assert result.decision is Decision.DENY

    def test_pass_through_unknown(self) -> None:
        tc = ToolCall(tool_name="custom_tool")
        result = allow_read_only(tc)
        assert result is None


class TestAllowPathPrefix:
    def test_allowed_path(self) -> None:
        policy = allow_path_prefix(("/home/project",))
        tc = ToolCall(tool_name="read", params={"file_path": "/home/project/src/main.py"})
        result = policy(tc)
        assert result is not None
        assert result.decision is Decision.ALLOW

    def test_denied_path(self) -> None:
        policy = allow_path_prefix(("/home/project",))
        tc = ToolCall(tool_name="read", params={"file_path": "/etc/passwd"})
        result = policy(tc)
        assert result is not None
        assert result.decision is Decision.DENY

    def test_no_path_param(self) -> None:
        policy = allow_path_prefix(("/safe",))
        tc = ToolCall(tool_name="search", params={"query": "hello"})
        result = policy(tc)
        assert result is None

    def test_windows_path_normalization(self) -> None:
        policy = allow_path_prefix(("C:\\Projects\\Safe",))
        tc = ToolCall(
            tool_name="read",
            params={"file_path": "C:/Projects/Safe/file.txt"},
        )
        result = policy(tc)
        assert result is not None
        assert result.decision is Decision.ALLOW

    def test_path_param_variants(self) -> None:
        """Should match on both 'file_path' and 'path' keys."""
        policy = allow_path_prefix(("/data",))
        tc1 = ToolCall(tool_name="glob", params={"path": "/data/logs"})
        assert policy(tc1) is not None
        assert policy(tc1).decision is Decision.ALLOW  # type: ignore[union-attr]


class TestDenyCommands:
    def test_deny_matching_pattern(self) -> None:
        policy = deny_commands(("rm -rf", "drop table"))
        tc = ToolCall(tool_name="bash", params={"command": "rm -rf /"})
        result = policy(tc)
        assert result is not None
        assert result.decision is Decision.DENY

    def test_allow_safe_params(self) -> None:
        policy = deny_commands(("rm -rf",))
        tc = ToolCall(tool_name="bash", params={"command": "ls -la"})
        result = policy(tc)
        assert result is None


class TestSecurityGate:
    def test_approve_with_policy(self) -> None:
        gate = SecurityGate()
        gate.add_policy(allow_read_only)

        tc = ToolCall(tool_name="read")
        result = gate.approve(tc)
        assert result.decision is Decision.ALLOW

    def test_deny_with_policy(self) -> None:
        gate = SecurityGate()
        gate.add_policy(allow_read_only)

        tc = ToolCall(tool_name="bash")
        result = gate.approve(tc)
        assert result.decision is Decision.DENY

    def test_default_require_approval(self) -> None:
        """Tools in require_approval_for get the default decision."""
        gate = SecurityGate(
            SecurityConfig(default_decision=Decision.ASK)
        )
        tc = ToolCall(tool_name="bash")
        result = gate.approve(tc)
        assert result.decision is Decision.ASK

    def test_default_no_restriction(self) -> None:
        """Tools NOT in require_approval_for are allowed by default."""
        gate = SecurityGate()
        tc = ToolCall(tool_name="custom_tool")
        result = gate.approve(tc)
        assert result.decision is Decision.ALLOW

    def test_policy_error_denies(self) -> None:
        def broken_policy(tc: ToolCall) -> ApprovalResult | None:
            raise ValueError("broken")

        gate = SecurityGate()
        gate.add_policy(broken_policy)

        tc = ToolCall(tool_name="read")
        result = gate.approve(tc)
        assert result.decision is Decision.DENY
        assert "Policy error" in result.reason

    def test_audit_log(self) -> None:
        gate = SecurityGate()
        gate.add_policy(allow_read_only)

        gate.approve(ToolCall(tool_name="read"))
        gate.approve(ToolCall(tool_name="write"))

        log = gate.audit_log
        assert len(log) == 2
        assert log[0].policy_name == "read_only"
        assert log[1].policy_name == "read_only"

    def test_policy_chain_order(self) -> None:
        """First non-None policy wins."""
        def first(tc: ToolCall) -> ApprovalResult | None:
            return ApprovalResult(Decision.ALLOW, "first", "first_policy")

        def second(tc: ToolCall) -> ApprovalResult | None:
            return ApprovalResult(Decision.DENY, "second", "second_policy")

        gate = SecurityGate()
        gate.add_policy(first)
        gate.add_policy(second)

        result = gate.approve(ToolCall(tool_name="bash"))
        assert result.decision is Decision.ALLOW
        assert result.policy_name == "first_policy"


# =============================================================================
# LLM Provider (MockProvider)
# =============================================================================


class TestMockProvider:
    def test_default_echo_response(self) -> None:
        provider = MockProvider()
        result = asyncio_run(
            provider.complete(
                messages=[{"role": "user", "content": "hello"}],
                tools=[],
                config=InferenceConfig(),
            )
        )
        assert "[mock]" in result.text
        assert "hello" in result.text

    def test_predefined_response(self) -> None:
        expected = InferenceResult(text="custom response", stop_reason="end_turn")
        provider = MockProvider(responses=[expected])

        result = asyncio_run(
            provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                config=InferenceConfig(),
            )
        )
        assert result.text == "custom response"

    def test_call_count(self) -> None:
        provider = MockProvider()
        asyncio_run(provider.complete([], [], InferenceConfig()))
        asyncio_run(provider.complete([], [], InferenceConfig()))
        assert provider.call_count == 2

    def test_last_messages_stored(self) -> None:
        provider = MockProvider()
        msgs = [{"role": "user", "content": "test"}]
        asyncio_run(provider.complete(msgs, [], InferenceConfig()))
        assert provider._last_messages == msgs

    def test_add_response_dynamic(self) -> None:
        provider = MockProvider()
        provider.add_response(InferenceResult(text="added later"))

        result = asyncio_run(
            provider.complete(
                messages=[{"role": "user", "content": "x"}],
                tools=[],
                config=InferenceConfig(),
            )
        )
        assert result.text == "added later"

    def test_multiple_responses_cycle(self) -> None:
        r1 = InferenceResult(text="first")
        r2 = InferenceResult(text="second")
        provider = MockProvider(responses=[r1, r2])

        result1 = asyncio_run(provider.complete([], [], InferenceConfig()))
        result2 = asyncio_run(provider.complete([], [], InferenceConfig()))
        result3 = asyncio_run(provider.complete([], [], InferenceConfig()))  # falls to default

        assert result1.text == "first"
        assert result2.text == "second"
        assert "[mock]" in result3.text


# =============================================================================
# Helpers
# =============================================================================


def asyncio_run(coro):
    """Run an async function synchronously for testing."""
    import asyncio
    return asyncio.run(coro)
