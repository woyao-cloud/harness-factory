"""Comprehensive tests for the ContextManager system.

Covers all 6 modules: types, store, compressor, dependency, restorer, manager.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from context_manager.types import (
    AnchorRef,
    BudgetState,
    CompressedRecord,
    CompressionCandidate,
    CompressionLevel,
    CompressionPlan,
    CompressTrigger,
    DependencyHit,
    Message,
    MessageType,
    RestorationRequest,
    RestoredSnippet,
    StoreLevel,
    ToolCall,
)

from context_manager.store import LayeredStore, StoreConfig
from context_manager.compressor import (
    ApplyResult,
    AsyncCompressor,
    CompressibilityScorer,
    CompressorConfig,
    StrategyChain,
    _discard_strategy,
    _summarize_strategy,
    _truncate_strategy,
)
from context_manager.dependency import (
    DependencyDetector,
    DetectorConfig,
    _find_file_paths,
    _find_key_term_matches,
    _find_line_numbers,
)
from context_manager.restorer import Restorer, RestorerConfig
from context_manager.manager import ContextManager, ManagerConfig


# =============================================================================
# types
# =============================================================================


class TestMessage:
    def test_defaults(self) -> None:
        m = Message()
        assert m.role == "user"
        assert m.content == ""
        assert m.msg_type == MessageType.USER_INPUT
        assert m.version == 1
        assert len(m.id) == 12

    def test_token_estimate(self) -> None:
        m = Message(content="a" * 100)
        assert m.token_estimate == 25  # 100 // 4

    def test_empty_token_estimate(self) -> None:
        m = Message()
        assert m.token_estimate == 0

    def test_frozen(self) -> None:
        m = Message(content="hello")
        with pytest.raises(AttributeError):
            m.content = "world"  # type: ignore[misc]

    def test_full_constructor(self) -> None:
        now = datetime.now(timezone.utc)
        m = Message(
            id="msg_001",
            role="assistant",
            content="Hello!",
            msg_type=MessageType.MODEL_REPLY,
            version=2,
            metadata={"key": "val"},
            timestamp=now,
        )
        assert m.id == "msg_001"
        assert m.role == "assistant"
        assert m.metadata["key"] == "val"
        assert m.timestamp == now


class TestStoreLevel:
    def test_values(self) -> None:
        assert StoreLevel.L1_MEMORY.value == "l1_memory"
        assert StoreLevel.L2_LOCAL.value == "l2_local"
        assert StoreLevel.L3_REMOTE.value == "l3_remote"


class TestCompressionLevel:
    def test_order(self) -> None:
        assert CompressionLevel.LEVEL_0_PRESERVE.value < CompressionLevel.LEVEL_4_STRUCTURED.value


class TestCompressTrigger:
    def test_all_values(self) -> None:
        assert CompressTrigger.BUDGET_WARN.name == "BUDGET_WARN"
        assert CompressTrigger.MANUAL.name == "MANUAL"


class TestMessageType:
    def test_coverage(self) -> None:
        types_r = {
            MessageType.SYSTEM,
            MessageType.USER_INPUT,
            MessageType.USER_INTENT,
            MessageType.MODEL_REPLY,
            MessageType.TOOL_CALL,
            MessageType.TOOL_RESULT,
            MessageType.FILE_CONTENT,
            MessageType.LOG_OUTPUT,
            MessageType.DIRECTORY_LISTING,
            MessageType.SEARCH_RESULT,
            MessageType.COMPRESSED_BLOCK,
            MessageType.SUPPLEMENTAL,
        }
        assert len(types_r) == 12


class TestAnchorRef:
    def test_defaults(self) -> None:
        a = AnchorRef(anchor_id="a1", session_id="s1")
        assert a.summary == ""
        assert a.source_message_ids == ()
        assert a.compressed_level == CompressionLevel.LEVEL_1_TRUNCATE

    def test_full(self) -> None:
        a = AnchorRef(
            anchor_id="a1",
            session_id="s1",
            source_path="/path/file.txt",
            source_message_ids=("m1", "m2"),
            summary="truncated output",
            compressed_level=CompressionLevel.LEVEL_2_SUMMARIZE,
            metadata={"key_terms": ["foo", "bar"]},
        )
        assert a.source_path == "/path/file.txt"
        assert a.metadata["key_terms"] == ["foo", "bar"]


class TestCompressedRecord:
    def test_defaults(self) -> None:
        r = CompressedRecord(anchor_id="a1", session_id="s1")
        assert r.store_level == StoreLevel.L2_LOCAL
        assert r.original_content == ""

    def test_full(self) -> None:
        r = CompressedRecord(
            record_id="rec_1",
            anchor_id="a1",
            session_id="s1",
            store_level=StoreLevel.L3_REMOTE,
            original_content="full text",
            summary="summary",
            message_ids=("m1",),
        )
        assert r.record_id == "rec_1"
        assert r.original_content == "full text"


class TestCompressionCandidate:
    def test_defaults(self) -> None:
        c = CompressionCandidate(
            message_id="m1",
            message_type=MessageType.TOOL_RESULT,
            content="data",
            token_count=100,
            score=0.75,
        )
        assert c.recommended_level == CompressionLevel.LEVEL_1_TRUNCATE


class TestCompressionPlan:
    def test_defaults(self) -> None:
        c = CompressionCandidate(message_id="m1", message_type=MessageType.USER_INPUT, content="x", token_count=10, score=0.5)
        plan = CompressionPlan(candidates=(c,))
        assert plan.trigger == CompressTrigger.BUDGET_COMPRESS
        assert plan.estimated_savings == 0


class TestBudgetState:
    def test_defaults(self) -> None:
        b = BudgetState()
        assert b.current_usage == 0
        assert b.ratio == 0.0
        assert b.trigger is None

    def test_remaining(self) -> None:
        b = BudgetState(current_usage=300, total_budget=1000)
        assert b.remaining == 700

    def test_zero_budget_remaining(self) -> None:
        b = BudgetState(current_usage=1000, total_budget=1000)
        assert b.remaining == 0


class TestToolCall:
    def test_defaults(self) -> None:
        tc = ToolCall(tool_name="read")
        assert tc.params == {}
        assert tc.raw_params == ""

    def test_full(self) -> None:
        tc = ToolCall(tool_name="write", params={"path": "f.txt"}, raw_params='{"path": "f.txt"}')
        assert tc.params["path"] == "f.txt"


class TestDependencyHit:
    def test_defaults(self) -> None:
        dh = DependencyHit(anchor_id="a1", confidence=0.8, match_type="file_path")
        assert dh.context_request == ""

    def test_full(self) -> None:
        dh = DependencyHit(
            anchor_id="a1",
            confidence=0.95,
            match_type="file_path",
            context_request="Find references to file",
        )
        assert dh.confidence == 0.95
        assert dh.match_type == "file_path"


class TestRestorationRequest:
    def test_defaults(self) -> None:
        rr = RestorationRequest(anchor_id="a1", context_request="line 42")
        assert rr.max_tokens == 2000


class TestRestoredSnippet:
    def test_defaults(self) -> None:
        rs = RestoredSnippet(anchor_id="a1", content="restored content")
        assert rs.source_path is None
        assert rs.truncated is False

    def test_format_injection_no_source(self) -> None:
        rs = RestoredSnippet(anchor_id="a1", content="hello")
        text = rs.format_injection()
        assert "[Restored: content]" in text
        assert "hello" in text

    def test_format_injection_with_source(self) -> None:
        rs = RestoredSnippet(
            anchor_id="a1",
            content="hello",
            source_path="/path/file.txt",
        )
        text = rs.format_injection()
        assert "/path/file.txt" in text

    def test_format_injection_truncated(self) -> None:
        rs = RestoredSnippet(
            anchor_id="a1",
            content="partial",
            truncated=True,
            total_original_tokens=100,
            restored_tokens=30,
        )
        text = rs.format_injection()
        assert "30/100 tokens restored" in text


# =============================================================================
# Store (LayeredStore)
# =============================================================================


class TestLayeredStore:
    def test_init_creates_db(self, tmp_path: Path) -> None:
        db = tmp_path / "test_store.sqlite"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        assert db.exists()
        store.close()

    def test_write_and_read_record(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        record = CompressedRecord(
            anchor_id="a1",
            session_id="s1",
            original_content="full content here",
            summary="summary text",
        )
        store.write(record)

        loaded = store.read("a1")
        assert loaded is not None
        assert loaded.original_content == "full content here"
        assert loaded.summary == "summary text"
        store.close()

    def test_read_nonexistent(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        assert store.read("no_such_anchor") is None
        store.close()

    def test_write_and_read_anchor(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        anchor = AnchorRef(
            anchor_id="anc_1",
            session_id="s1",
            source_path="/path/file.py",
            source_message_ids=("m1", "m2"),
            summary="compressed block",
            compressed_level=CompressionLevel.LEVEL_2_SUMMARIZE,
            metadata={"key_terms": ["foo", "bar"]},
        )
        store.write_anchor(anchor)

        loaded = store.read_anchor("anc_1")
        assert loaded is not None
        assert loaded.source_path == "/path/file.py"
        assert loaded.source_message_ids == ("m1", "m2")
        assert loaded.metadata["key_terms"] == ["foo", "bar"]
        store.close()

    def test_read_anchor_nonexistent(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        assert store.read_anchor("no_such") is None
        store.close()

    def test_restore_snippet_no_query(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        record = CompressedRecord(
            anchor_id="a1",
            session_id="s1",
            original_content="Hello World! " * 500,
            metadata={"source_path": "test.txt"},
        )
        store.write(record)

        snippet = store.restore_snippet("a1", max_tokens=100)
        assert snippet is not None
        assert snippet.restored_tokens > 0
        store.close()

    def test_restore_snippet_line_range(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        lines = "\n".join(f"line {i}" for i in range(1, 101))
        record = CompressedRecord(anchor_id="a1", session_id="s1", original_content=lines)
        store.write(record)

        snippet = store.restore_snippet("a1", query="lines 10-15")
        assert snippet is not None
        assert "line 10" in snippet.content
        assert "line 15" in snippet.content
        assert "line 16" not in snippet.content
        store.close()

    def test_restore_snippet_single_line(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        content = "\n".join(f"line {i}" for i in range(1, 101))
        record = CompressedRecord(anchor_id="a1", session_id="s1", original_content=content)
        store.write(record)

        snippet = store.restore_snippet("a1", query="line 42")
        assert snippet is not None
        assert "line 42" in snippet.content
        store.close()

    def test_restore_snippet_keyword(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        content = "\n".join(f"data row {i}" for i in range(20))
        content += "\nERROR: something broke"
        content += "\n".join(f"after row {i}" for i in range(10))
        record = CompressedRecord(anchor_id="a1", session_id="s1", original_content=content)
        store.write(record)

        snippet = store.restore_snippet("a1", query="ERROR")
        assert snippet is not None
        assert "ERROR" in snippet.content
        store.close()

    def test_restore_snippet_nonexistent_anchor(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        assert store.restore_snippet("no_such") is None
        store.close()

    def test_cross_session_lookup(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        a = AnchorRef(anchor_id="a1", session_id="s1", source_path="/common/file.txt")
        store.write_anchor(a)

        results = store.cross_session_lookup("/common/file.txt")
        assert len(results) == 1

        results_scoped = store.cross_session_lookup("/common/file.txt", session_id="s2")
        assert len(results_scoped) == 0
        store.close()

    def test_list_session_anchors(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        store.write_anchor(AnchorRef(anchor_id="a1", session_id="s1"))
        store.write_anchor(AnchorRef(anchor_id="a2", session_id="s1"))
        store.write_anchor(AnchorRef(anchor_id="a3", session_id="s2"))

        anchors = store.list_session_anchors("s1")
        assert len(anchors) == 2
        store.close()

    def test_list_session_records(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        store.write(CompressedRecord(anchor_id="a1", session_id="s1"))
        store.write(CompressedRecord(anchor_id="a2", session_id="s1"))

        records = store.list_session_records("s1")
        assert len(records) == 2
        store.close()

    def test_delete_session(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        store.write_anchor(AnchorRef(anchor_id="a1", session_id="s1"))
        store.write(CompressedRecord(anchor_id="a1", session_id="s1"))

        store.delete_session("s1")

        assert store.read_anchor("a1") is None
        assert store.read("a1") is None
        store.close()

    def test_store_path_property(self, tmp_path: Path) -> None:
        db = tmp_path / "custom_path.sqlite"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        assert store.store_path == str(db)
        store.close()

    def test_close_twice(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        store.close()
        store.close()  # should not raise


# =============================================================================
# Compressor
# =============================================================================


class TestCompressibilityScorer:
    def test_empty_messages(self) -> None:
        scorer = CompressibilityScorer(CompressorConfig())
        candidates = scorer.score([])
        assert candidates == []

    def test_skips_system_and_user_intent(self) -> None:
        scorer = CompressibilityScorer(CompressorConfig())
        msgs = [
            Message(role="system", msg_type=MessageType.SYSTEM, content="x" * 200),
            Message(role="user", msg_type=MessageType.USER_INTENT, content="x" * 200),
        ]
        candidates = scorer.score(msgs)
        # Neither should be scored
        assert len(candidates) == 0

    def test_skips_already_compressed(self) -> None:
        scorer = CompressibilityScorer(CompressorConfig())
        msgs = [
            Message(role="user", msg_type=MessageType.COMPRESSED_BLOCK, content="x" * 200),
        ]
        candidates = scorer.score(msgs)
        assert len(candidates) == 0

    def test_skips_small_messages(self) -> None:
        scorer = CompressibilityScorer(CompressorConfig())
        msgs = [
            Message(role="user", content="short", msg_type=MessageType.TOOL_RESULT),
        ]
        candidates = scorer.score(msgs)
        assert len(candidates) == 0

    def test_scores_tool_result(self) -> None:
        scorer = CompressibilityScorer(CompressorConfig())
        msgs = [
            Message(role="user", content="x" * 400, msg_type=MessageType.TOOL_RESULT),
        ]
        candidates = scorer.score(msgs)
        assert len(candidates) == 1
        assert candidates[0].score > 0

    def test_type_bonus_highest_for_logs(self) -> None:
        scorer = CompressibilityScorer(CompressorConfig())
        bonus = scorer._type_bonus(MessageType.LOG_OUTPUT)
        assert bonus == 0.9
        bonus = scorer._type_bonus(MessageType.USER_INPUT)
        assert bonus == 0.1

    def test_recommend_level(self) -> None:
        scorer = CompressibilityScorer(CompressorConfig())
        assert scorer._recommend_level(0.9, 2000) == CompressionLevel.LEVEL_3_DISCARD
        assert scorer._recommend_level(0.7, 500) == CompressionLevel.LEVEL_2_SUMMARIZE
        assert scorer._recommend_level(0.3, 100) == CompressionLevel.LEVEL_1_TRUNCATE

    def test_tool_result_bonus(self) -> None:
        scorer = CompressibilityScorer(CompressorConfig())
        # Non-tool-result should be 0
        msg = Message(content="test", msg_type=MessageType.USER_INPUT)
        assert scorer._tool_result_bonus(msg) == 0.0

        # Large tool result
        msg = Message(content="x" * 8000, msg_type=MessageType.TOOL_RESULT)
        bonus = scorer._tool_result_bonus(msg)
        assert bonus > 0

    def test_candidates_sorted_by_score_desc(self) -> None:
        scorer = CompressibilityScorer(CompressorConfig())
        msgs = [
            Message(content="a" * 400, msg_type=MessageType.LOG_OUTPUT),  # high bonus
            Message(content="b" * 400, msg_type=MessageType.USER_INPUT),  # low bonus
        ]
        candidates = scorer.score(msgs)
        assert len(candidates) == 2
        assert candidates[0].score >= candidates[1].score


class TestStrategyChain:
    def test_default_strategies(self) -> None:
        chain = StrategyChain()
        assert CompressionLevel.LEVEL_1_TRUNCATE in chain.strategies
        assert CompressionLevel.LEVEL_2_SUMMARIZE in chain.strategies
        assert CompressionLevel.LEVEL_3_DISCARD in chain.strategies

    def test_get_known_strategy(self) -> None:
        chain = StrategyChain()
        strategy = chain.get(CompressionLevel.LEVEL_1_TRUNCATE)
        assert callable(strategy)

    def test_get_unknown_falls_back(self) -> None:
        chain = StrategyChain()
        strategy = chain.get(CompressionLevel.LEVEL_4_STRUCTURED)
        assert callable(strategy)  # falls back to _truncate_strategy


class TestTruncateStrategy:
    def test_truncates_long_lines(self) -> None:
        msg = Message(content="\n".join(f"line {i}" for i in range(100)), msg_type=MessageType.TOOL_RESULT)
        candidate = CompressionCandidate(
            message_id=msg.id,
            message_type=MessageType.TOOL_RESULT,
            content=msg.content,
            token_count=msg.token_estimate,
            score=0.8,
            recommended_level=CompressionLevel.LEVEL_1_TRUNCATE,
        )
        cfg = CompressorConfig(max_truncate_lines=10)
        result = _truncate_strategy(candidate, msg, cfg)

        assert result.replacement is not None
        assert "anchor:" in result.replacement.content
        assert result.savings > 0
        assert result.record is not None
        assert result.anchor is not None

    def test_truncates_long_content(self) -> None:
        msg = Message(content="x" * 5000, msg_type=MessageType.TOOL_RESULT)
        candidate = CompressionCandidate(
            message_id=msg.id,
            message_type=MessageType.TOOL_RESULT,
            content=msg.content,
            token_count=msg.token_estimate,
            score=0.8,
            recommended_level=CompressionLevel.LEVEL_1_TRUNCATE,
        )
        cfg = CompressorConfig(max_truncate_chars=100)
        result = _truncate_strategy(candidate, msg, cfg)

        assert result.replacement is not None
        assert len(result.replacement.content) < len(msg.content)

    def test_no_truncation_needed(self) -> None:
        msg = Message(content="short", msg_type=MessageType.USER_INPUT)
        candidate = CompressionCandidate(
            message_id=msg.id,
            message_type=MessageType.USER_INPUT,
            content="short",
            token_count=1,
            score=0.1,
            recommended_level=CompressionLevel.LEVEL_1_TRUNCATE,
        )
        result = _truncate_strategy(candidate, msg, CompressorConfig())
        assert result.savings == 0


class TestSummarizeStrategy:
    def test_creates_summary(self) -> None:
        msg = Message(content="\n".join(f"data row {i}" for i in range(50)), msg_type=MessageType.TOOL_RESULT)
        candidate = CompressionCandidate(
            message_id=msg.id,
            message_type=MessageType.TOOL_RESULT,
            content=msg.content,
            token_count=msg.token_estimate,
            score=0.7,
            recommended_level=CompressionLevel.LEVEL_2_SUMMARIZE,
        )
        result = _summarize_strategy(candidate, msg, CompressorConfig())

        assert result.replacement is not None
        assert "[Compressed:" in result.replacement.content
        assert result.record is not None
        assert "summarize" in result.record.metadata["level"]


class TestDiscardStrategy:
    def test_discards_message(self) -> None:
        msg = Message(content="completed task output", msg_type=MessageType.TOOL_RESULT)
        candidate = CompressionCandidate(
            message_id=msg.id,
            message_type=MessageType.TOOL_RESULT,
            content=msg.content,
            token_count=10,
            score=0.9,
            recommended_level=CompressionLevel.LEVEL_3_DISCARD,
        )
        result = _discard_strategy(candidate, msg, CompressorConfig())

        assert result.replacement is None  # fully removed
        assert result.record is not None
        assert result.anchor is not None
        assert result.savings == 10


class TestApplyResult:
    def test_defaults(self) -> None:
        r = ApplyResult(messages=(), records=(), anchors=())
        assert r.message_count == 0
        assert r.savings == 0

    def test_message_count(self) -> None:
        msgs = (Message(content="a"), Message(content="b"))
        r = ApplyResult(messages=msgs, records=(), anchors=())
        assert r.message_count == 2


class TestAsyncCompressor:
    def test_score_empty(self) -> None:
        comp = AsyncCompressor(CompressorConfig())
        candidates = comp.score([])
        assert candidates == []

    def test_score_messages(self) -> None:
        comp = AsyncCompressor(CompressorConfig())
        msgs = [Message(content="x" * 400, msg_type=MessageType.TOOL_RESULT)]
        candidates = comp.score(msgs)
        assert len(candidates) >= 1

    def test_create_plan_empty(self) -> None:
        comp = AsyncCompressor(CompressorConfig())
        plan = comp.create_plan([], trigger=CompressTrigger.MANUAL)
        assert len(plan.candidates) == 0
        assert plan.estimated_savings == 0

    def test_create_plan_with_messages(self) -> None:
        comp = AsyncCompressor(CompressorConfig())
        msgs = [
            Message(content="x" * 400, msg_type=MessageType.TOOL_RESULT),
            Message(content="y" * 400, msg_type=MessageType.LOG_OUTPUT),
        ]
        plan = comp.create_plan(msgs, trigger=CompressTrigger.BUDGET_COMPRESS)
        assert len(plan.candidates) > 0
        assert plan.estimated_savings > 0
        assert plan.trigger == CompressTrigger.BUDGET_COMPRESS

    def test_create_plan_with_target_savings(self) -> None:
        comp = AsyncCompressor(CompressorConfig(max_candidates_per_round=10))
        msgs = [
            Message(content="x" * 400, msg_type=MessageType.TOOL_RESULT),
            Message(content="y" * 400, msg_type=MessageType.TOOL_RESULT),
        ]
        plan = comp.create_plan(msgs, target_savings=50)
        assert len(plan.candidates) > 0

    def test_submit_and_collect_plan(self) -> None:
        comp = AsyncCompressor(CompressorConfig())
        msgs = [Message(content="x" * 400, msg_type=MessageType.TOOL_RESULT)]

        assert comp.collect_plan() is None  # nothing yet

        comp.submit_plan(msgs, trigger=CompressTrigger.BUDGET_WARN)
        plan = comp.collect_plan()
        assert plan is not None
        assert plan.trigger == CompressTrigger.BUDGET_WARN

        # Second collect should return None
        assert comp.collect_plan() is None

    def test_apply_plan(self) -> None:
        comp = AsyncCompressor(CompressorConfig(max_candidates_per_round=5))
        msgs = [Message(content="x" * 400, msg_type=MessageType.TOOL_RESULT)]
        plan = comp.create_plan(msgs)

        result = comp.apply_plan(plan, msgs, session_id="s1")
        assert len(result.messages) > 0
        assert len(result.records) > 0
        assert len(result.anchors) > 0
        assert result.savings > 0

    def test_apply_plan_no_candidates(self) -> None:
        comp = AsyncCompressor(CompressorConfig())
        plan = comp.create_plan([])
        result = comp.apply_plan(plan, [], session_id="s1")
        assert result.savings == 0
        assert result.message_count == 0


# =============================================================================
# Dependency
# =============================================================================


class TestFindFilePaths:
    def test_direct_file_path_param(self) -> None:
        tc = ToolCall(tool_name="read", params={"file_path": "/home/file.txt"})
        paths = _find_file_paths(tc)
        assert any(p == "/home/file.txt" for p, _ in paths)

    def test_path_param_key(self) -> None:
        tc = ToolCall(tool_name="glob", params={"path": "/home/src"})
        paths = _find_file_paths(tc)
        assert any(p == "/home/src" for p, _ in paths)

    def test_source_param_key(self) -> None:
        tc = ToolCall(tool_name="edit", params={"source": "/tmp/test.py"})
        paths = _find_file_paths(tc)
        assert any(p == "/tmp/test.py" for p, _ in paths)

    def test_regex_path_detection(self) -> None:
        tc = ToolCall(tool_name="bash", params={"command": "cat /etc/config.yaml"})
        paths = _find_file_paths(tc)
        assert any("config.yaml" in p for p, _ in paths)


class TestFindLineNumbers:
    def test_direct_line_param(self) -> None:
        tc = ToolCall(tool_name="read", params={"line": 42})
        refs = _find_line_numbers(tc)
        assert (42, None, 0.9) in refs

    def test_line_start_end(self) -> None:
        tc = ToolCall(tool_name="read", params={"line_start": 10, "line_end": 20})
        refs = _find_line_numbers(tc)
        assert any(r[0] == 10 and r[1] == 20 for r in refs)

    def test_regex_in_string(self) -> None:
        tc = ToolCall(tool_name="edit", params={"old_string": "see line 55 for details"})
        refs = _find_line_numbers(tc)
        assert any(r[0] == 55 for r in refs)

    def test_no_line_refs(self) -> None:
        tc = ToolCall(tool_name="read", params={"path": "file.txt"})
        refs = _find_line_numbers(tc)
        assert len(refs) == 0


class TestFindKeyTermMatches:
    def test_matches_key_terms(self) -> None:
        anchors = [
            AnchorRef(
                anchor_id="a1",
                session_id="s1",
                metadata={"key_terms": ["error", "timeout", "retry"]},
            )
        ]
        tc = ToolCall(tool_name="grep", params={"pattern": "error timeout"})
        hits = _find_key_term_matches(tc, anchors)
        assert len(hits) > 0
        assert hits[0][0] == "a1"

    def test_no_match(self) -> None:
        anchors = [
            AnchorRef(
                anchor_id="a1",
                session_id="s1",
                metadata={"key_terms": ["unrelated"]},
            )
        ]
        tc = ToolCall(tool_name="read", params={"path": "file.txt"})
        hits = _find_key_term_matches(tc, anchors)
        assert len(hits) == 0

    def test_empty_key_terms(self) -> None:
        anchors = [AnchorRef(anchor_id="a1", session_id="s1")]
        tc = ToolCall(tool_name="read", params={"path": "test.py"})
        hits = _find_key_term_matches(tc, anchors)
        assert len(hits) == 0


class TestDependencyDetector:
    def test_detect_file_path_match(self) -> None:
        detector = DependencyDetector(DetectorConfig())
        tc = ToolCall(tool_name="read", params={"file_path": "/project/main.py"})
        anchors = [
            AnchorRef(
                anchor_id="a1",
                session_id="s1",
                source_path="/project/main.py",
            )
        ]
        hits = detector.detect(tc, anchors)
        assert len(hits) == 1
        assert hits[0].match_type == "file_path"

    def test_detect_no_match(self) -> None:
        detector = DependencyDetector(DetectorConfig())
        tc = ToolCall(tool_name="read", params={"file_path": "/other/file.txt"})
        anchors = [
            AnchorRef(anchor_id="a1", session_id="s1", source_path="/project/main.py")
        ]
        hits = detector.detect(tc, anchors)
        assert len(hits) == 0

    def test_path_matches_multiple_variants(self) -> None:
        assert DependencyDetector._path_matches("/project/main.py", "/project/main.py") is True
        assert DependencyDetector._path_matches("main.py", "/project/main.py") is True
        assert DependencyDetector._path_matches("/project/main.py", "main.py") is True

    def test_detect_line_number(self) -> None:
        detector = DependencyDetector(DetectorConfig())
        tc = ToolCall(tool_name="read", params={"file_path": "/project/main.py", "line": 42})
        anchors = [
            AnchorRef(
                anchor_id="a1",
                session_id="s1",
                source_path="/project/main.py",
            )
        ]
        hits = detector.detect(tc, anchors)
        assert any(h.match_type == "line_number" for h in hits)

    def test_detect_key_term(self) -> None:
        detector = DependencyDetector(DetectorConfig())
        tc = ToolCall(tool_name="grep", params={"pattern": "error timeout"})
        anchors = [
            AnchorRef(
                anchor_id="a1",
                session_id="s1",
                source_path="/project/main.py",
                metadata={"key_terms": ["error", "timeout"]},
            )
        ]
        hits = detector.detect(tc, anchors)
        assert any(h.match_type == "key_term" for h in hits)

    def test_dedup_anchors(self) -> None:
        """Same anchor should not appear twice even if matched by multiple detectors."""
        detector = DependencyDetector(DetectorConfig())
        tc = ToolCall(
            tool_name="read",
            params={"file_path": "/project/main.py", "line": 10},
        )
        anchors = [
            AnchorRef(
                anchor_id="a1",
                session_id="s1",
                source_path="/project/main.py",
                metadata={"key_terms": ["main"]},
            )
        ]
        hits = detector.detect(tc, anchors)
        # Should only have one hit per anchor
        anchor_ids = [h.anchor_id for h in hits]
        assert len(set(anchor_ids)) == len(anchor_ids)

    def test_empty_anchors(self) -> None:
        detector = DependencyDetector(DetectorConfig())
        tc = ToolCall(tool_name="read", params={"file_path": "test.py"})
        hits = detector.detect(tc, [])
        assert hits == []


# =============================================================================
# Restorer
# =============================================================================


class TestRestorer:
    def test_restore_no_hits(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        restorer = Restorer(RestorerConfig())

        snippets = restorer.restore([], store)
        assert snippets == []
        store.close()

    def test_restore_with_hits(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        store.write(
            CompressedRecord(anchor_id="a1", session_id="s1", original_content="Hello World")
        )

        restorer = Restorer(RestorerConfig())
        hits = [
            DependencyHit(anchor_id="a1", confidence=0.9, match_type="file_path"),
        ]
        snippets = restorer.restore(hits, store)
        assert len(snippets) == 1
        assert snippets[0].content == "Hello World"
        store.close()

    def test_restore_respects_max_snippets(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        store.write(CompressedRecord(anchor_id="a1", session_id="s1", original_content="A"))
        store.write(CompressedRecord(anchor_id="a2", session_id="s1", original_content="B"))
        store.write(CompressedRecord(anchor_id="a3", session_id="s1", original_content="C"))

        restorer = Restorer(RestorerConfig(max_snippets_per_round=2))
        hits = [
            DependencyHit(anchor_id="a1", confidence=0.9, match_type="file_path"),
            DependencyHit(anchor_id="a2", confidence=0.8, match_type="file_path"),
            DependencyHit(anchor_id="a3", confidence=0.7, match_type="file_path"),
        ]
        snippets = restorer.restore(hits, store)
        assert len(snippets) == 2
        store.close()

    def test_format_supplemental_context_empty(self) -> None:
        restorer = Restorer(RestorerConfig())
        text = restorer.format_supplemental_context([])
        assert text == ""

    def test_format_supplemental_context_with_snippets(self) -> None:
        restorer = Restorer(RestorerConfig())
        snippets = [
            RestoredSnippet(anchor_id="a1", content="restored content", source_path="file.py"),
        ]
        text = restorer.format_supplemental_context(snippets)
        assert "<supplemental_context>" in text
        assert "file.py" in text
        assert "restored content" in text
        assert "</supplemental_context>" in text

    def test_format_multiple_snippets(self) -> None:
        restorer = Restorer(RestorerConfig())
        snippets = [
            RestoredSnippet(anchor_id="a1", content="first"),
            RestoredSnippet(anchor_id="a2", content="second"),
        ]
        text = restorer.format_supplemental_context(snippets)
        # Should reference both blocks
        assert "block 1" in text
        assert "block 2" in text

    def test_degrade_with_source_path(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        store.write_anchor(
            AnchorRef(anchor_id="a1", session_id="s1", source_path="/path/file.txt")
        )

        restorer = Restorer(RestorerConfig())
        hit = DependencyHit(anchor_id="a1", confidence=0.9, match_type="file_path")

        snippet = restorer.degrade(hit, store)
        assert snippet is not None
        assert "re-read it from disk" in snippet.content
        store.close()

    def test_degrade_without_source_path(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        store.write_anchor(
            AnchorRef(anchor_id="a1", session_id="s1", summary="some summary")
        )

        restorer = Restorer(RestorerConfig())
        hit = DependencyHit(anchor_id="a1", confidence=0.9, match_type="file_path")

        snippet = restorer.degrade(hit, store)
        assert snippet is not None
        assert "some summary" in snippet.content
        store.close()

    def test_degrade_unknown_anchor(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))

        restorer = Restorer(RestorerConfig())
        hit = DependencyHit(anchor_id="no_such", confidence=0.9, match_type="file_path")

        snippet = restorer.degrade(hit, store)
        assert snippet is None
        store.close()


# =============================================================================
# Manager (ContextManager)
# =============================================================================


class TestContextManager:
    def test_initial_state(self) -> None:
        cm = ContextManager(ManagerConfig(total_budget=10000))
        assert cm.active_messages == ()
        assert cm.session_id is not None

    def test_add_message(self) -> None:
        cm = ContextManager(ManagerConfig(total_budget=10000))
        msg = Message(content="hello", msg_type=MessageType.USER_INPUT)
        returned = cm.add_message(msg)
        assert returned.id == msg.id
        assert returned.metadata["session_id"] == cm.session_id
        assert len(cm.active_messages) == 1

    def test_add_message_closed_raises(self) -> None:
        cm = ContextManager(ManagerConfig(total_budget=10000))
        cm.close()
        with pytest.raises(RuntimeError, match="closed"):
            cm.add_message(Message(content="test"))

    def test_budget_initial(self) -> None:
        cm = ContextManager(ManagerConfig(total_budget=10000))
        b = cm.budget
        assert b.current_usage == 0
        assert b.ratio == 0.0
        assert b.trigger is None

    def test_budget_after_messages(self) -> None:
        cm = ContextManager(ManagerConfig(total_budget=10000))
        cm.add_message(Message(content="x" * 2000, msg_type=MessageType.USER_INPUT))
        b = cm.budget
        assert b.current_usage > 0
        assert b.ratio > 0

    def test_budget_trigger_warn(self) -> None:
        cm = ContextManager(ManagerConfig(total_budget=1000, warn_ratio=0.5))
        # Add enough to trigger warn
        cm.add_message(Message(content="x" * 2000, msg_type=MessageType.TOOL_RESULT))
        b = cm.budget
        # Should be at warn or higher
        assert b.trigger is not None

    def test_budget_trigger_hard_limit(self, tmp_path: Path) -> None:
        """Hard limit should trigger emergency compression."""
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        cm = ContextManager(
            ManagerConfig(total_budget=500, hard_limit_ratio=0.5, compress_ratio=0.4),
            store=store,
        )
        # Add a very large message to blow past hard limit
        msg = Message(content="x" * 3000, msg_type=MessageType.TOOL_RESULT)
        cm.add_message(msg)
        # Compression should have been triggered, but not crash
        assert cm.active_messages is not None
        store.close()

    def test_safe_point_before_inference_noop(self) -> None:
        cm = ContextManager(ManagerConfig(total_budget=10000))
        result = cm.safe_point_before_inference()
        assert result is None

    def test_close(self) -> None:
        cm = ContextManager(ManagerConfig(total_budget=10000))
        cm.close()
        assert cm._closed is True

    def test_close_idempotent(self) -> None:
        cm = ContextManager(ManagerConfig(total_budget=10000))
        cm.close()
        cm.close()  # should not raise

    def test_reset(self) -> None:
        cm = ContextManager(ManagerConfig(total_budget=10000))
        cm.add_message(Message(content="hello"))
        assert len(cm.active_messages) == 1
        cm.reset()
        assert cm.active_messages == ()
        assert cm._active_anchors == {}

    def test_store_path_property(self, tmp_path: Path) -> None:
        db = tmp_path / "cm_store.sqlite"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        cm = ContextManager(ManagerConfig(total_budget=10000), store=store)
        assert cm.store_path == str(db)

    def test_lookup_anchor(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        store.write_anchor(
            AnchorRef(anchor_id="a1", session_id="s1", source_path="/path/file.txt")
        )
        cm = ContextManager(ManagerConfig(total_budget=10000, session_id="s1"), store=store)
        results = cm.lookup_anchor("/path/file.txt")
        assert len(results) == 1

    def test_export_session_history(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        cm = ContextManager(
            ManagerConfig(total_budget=10000, session_id="s1"),
            store=store,
        )
        records, anchors = cm.export_session_history()
        assert isinstance(records, list)
        assert isinstance(anchors, list)

    def test_record_tool_call_no_anchors(self) -> None:
        """record_tool_call should not crash when there are no active anchors."""
        cm = ContextManager(ManagerConfig(total_budget=10000))
        cm.record_tool_call("read", {"path": "test.txt"})
        # No crash = success

    def test_build_prompt_basic(self) -> None:
        cm = ContextManager(ManagerConfig(total_budget=10000))
        cm.add_message(Message(content="hello", msg_type=MessageType.USER_INPUT))
        prompt = cm.build_prompt()
        assert len(prompt) >= 1
        assert prompt[0].content == "hello"

    def test_build_prompt_with_supplemental(self, tmp_path: Path) -> None:
        """build_prompt should inject supplemental context when pending."""
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        cm = ContextManager(ManagerConfig(total_budget=10000), store=store)

        # Add messages
        cm.add_message(Message(content="first", msg_type=MessageType.USER_INPUT))
        cm.add_message(Message(content="last", msg_type=MessageType.USER_INPUT))

        # Manually set pending supplemental
        cm._pending_supplemental = [
            RestoredSnippet(anchor_id="a1", content="restored context")
        ]

        prompt = cm.build_prompt()
        # Should have injected as system message before last user message
        assert len(prompt) == 3  # first + supplemental + last
        assert prompt[0].content == "first"
        assert "<supplemental_context>" in prompt[1].content
        assert prompt[2].content == "last"
        # Pending should be cleared
        assert cm._pending_supplemental == []
        store.close()

    def test_safe_point_after_tool(self, tmp_path: Path) -> None:
        """safe_point_after_tool should detect dependencies and queue supplemental."""
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        cm = ContextManager(ManagerConfig(total_budget=10000), store=store)

        # Add an active anchor
        cm._active_anchors["a1"] = AnchorRef(
            anchor_id="a1",
            session_id=cm.session_id,
            source_path="/path/file.txt",
        )

        tc = ToolCall(tool_name="read", params={"file_path": "/path/file.txt"})
        cm.safe_point_after_tool(tc)
        store.close()

    def test_compression_through_add_message(self, tmp_path: Path) -> None:
        """Adding enough messages should trigger compression."""
        db = tmp_path / "test.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        cm = ContextManager(
            ManagerConfig(total_budget=500, compress_ratio=0.3),
            store=store,
        )
        # Add enough content to trigger compression
        for _ in range(3):
            cm.add_message(
                Message(content="x" * 400, msg_type=MessageType.TOOL_RESULT)
            )
        # Should have triggered compression
        store.close()

    def test_session_id_generated(self) -> None:
        cm1 = ContextManager(ManagerConfig(total_budget=10000))
        cm2 = ContextManager(ManagerConfig(total_budget=10000))
        assert cm1.session_id != cm2.session_id

    def test_custom_session_id(self) -> None:
        cm = ContextManager(ManagerConfig(total_budget=10000, session_id="my_session"))
        assert cm.session_id == "my_session"

    def test_active_messages_immutable(self) -> None:
        cm = ContextManager(ManagerConfig(total_budget=10000))
        cm.add_message(Message(content="test"))
        msgs = cm.active_messages
        assert isinstance(msgs, tuple)

    def test_message_enriched_with_session_id(self) -> None:
        cm = ContextManager(ManagerConfig(total_budget=10000, session_id="s_test"))
        msg = Message(content="hello")
        returned = cm.add_message(msg)
        assert returned.metadata["session_id"] == "s_test"


# =============================================================================
# Integration: compressor + store round-trip
# =============================================================================


class TestCompressorStoreIntegration:
    def test_compress_and_restore(self, tmp_path: Path) -> None:
        """Full round-trip: compress messages, persist to store, restore snippet."""
        db = tmp_path / "integration.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        comp = AsyncCompressor(CompressorConfig(max_candidates_per_round=5))

        # Create messages
        msgs = [
            Message(content="x" * 400, msg_type=MessageType.TOOL_RESULT),
            Message(content="y" * 400, msg_type=MessageType.LOG_OUTPUT),
        ]

        # Plan and apply
        plan = comp.create_plan(msgs)
        result = comp.apply_plan(plan, msgs, session_id="s_int")

        # Persist
        for record in result.records:
            store.write(record)

        # Restore
        for record in result.records:
            snippet = store.restore_snippet(record.anchor_id)
            assert snippet is not None
            assert len(snippet.content) > 0

        store.close()

    def test_full_context_manager_lifecycle(self, tmp_path: Path) -> None:
        """End-to-end: ContextManager with add_message, build_prompt, close."""
        db = tmp_path / "e2e.db"
        store = LayeredStore(StoreConfig(db_path=str(db)))
        cm = ContextManager(
            ManagerConfig(total_budget=10000, session_id="e2e_test"),
            store=store,
        )

        # Add messages
        cm.add_message(Message(content="User query", msg_type=MessageType.USER_INPUT))
        cm.add_message(Message(content="Model reply", msg_type=MessageType.MODEL_REPLY))
        cm.add_message(
            Message(content="x" * 500, msg_type=MessageType.TOOL_RESULT)
        )

        # Build prompt
        prompt = cm.build_prompt()
        assert len(prompt) == 3

        # Record tool call (no anchors, no crash)
        cm.record_tool_call("read", {"path": "test.py"})

        # Budget should reflect usage
        b = cm.budget
        assert b.current_usage > 0
        assert b.ratio > 0

        # Export
        records, anchors = cm.export_session_history()
        assert isinstance(records, list)

        # Close
        cm.close()
        assert cm._closed

        store.close()


# =============================================================================
# Helpers
# =============================================================================


# (no async helpers needed — all context_manager tests are synchronous)
