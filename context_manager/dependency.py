"""Tool-call dependency detection — sniffing references to compressed content."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .types import AnchorRef, DependencyHit, ToolCall


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DetectorConfig:
    file_path_pattern: str = r"(?:^|[\s\"\'=])([\/\w\-\.]+(?:\.\w+)+)"  # noqa
    line_ref_pattern: str = r"(?:line|lines?)\s*(\d+)(?:\s*[-–to]+\s*(\d+))?"  # noqa
    min_confidence_file: float = 0.7
    min_confidence_line: float = 0.5
    min_confidence_term: float = 0.3


# ── Pattern matchers ─────────────────────────────────────────────────────────


def _find_file_paths(tool_call: ToolCall) -> list[tuple[str, float]]:
    """Extract likely file paths from tool parameters."""
    raw = str(tool_call.params)
    paths: list[tuple[str, float]] = []

    # Direct file_path / path parameter
    for key in ("file_path", "path", "source", "target", "dest"):
        val = tool_call.params.get(key)
        if isinstance(val, str) and val.strip():
            paths.append((val.strip(), 0.95))

    # Regex scan of raw params for path-like strings
    for m in re.finditer(r'(?:^|[\s"\'=])(([\/\w\-\.]+\.[\w]+))', raw):
        stripped = m.group(1).lstrip("=\"\'")
        if stripped and not stripped.startswith("--"):
            paths.append((stripped, 0.7))

    return paths


def _find_line_numbers(tool_call: ToolCall) -> list[tuple[int, int | None, float]]:
    """Extract line number references from tool parameters."""
    results: list[tuple[int, int | None, float]] = []

    # Direct line parameter
    line_val = tool_call.params.get("line")
    if isinstance(line_val, (int, float)) and line_val > 0:
        results.append((int(line_val), None, 0.9))

    # line_start / line_end
    start = tool_call.params.get("line_start") or tool_call.params.get("start_line")
    end = tool_call.params.get("line_end") or tool_call.params.get("end_line")
    if start is not None and isinstance(start, (int, float)) and start > 0:
        end_val = int(end) if end is not None and isinstance(end, (int, float)) else None
        results.append((int(start), end_val, 0.85))

    # Regex in string params
    for val in tool_call.params.values():
        if isinstance(val, str):
            for m in re.finditer(r"(?:line|lines?)\s*(\d+)(?:\s*[-–to]+\s*(\d+))?", val, re.IGNORECASE):
                s = int(m.group(1))
                e = int(m.group(2)) if m.group(2) else None
                results.append((s, e, 0.7))

    return results


def _find_key_term_matches(
    tool_call: ToolCall, anchors: list[AnchorRef]
) -> list[tuple[str, float]]:
    """Match tool call parameters against anchor key_terms."""
    raw = str(tool_call.params).lower()
    hits: list[tuple[str, float]] = []
    for anchor in anchors:
        terms = anchor.metadata.get("key_terms", [])
        if not isinstance(terms, list):
            continue
        matched = sum(1 for t in terms if isinstance(t, str) and t.lower() in raw)
        if matched > 0:
            confidence = min(matched / max(len(terms), 1) * 2, 0.9)
            hits.append((anchor.anchor_id, confidence))
    return hits


# ── DependencyDetector ──────────────────────────────────────────────────────


class DependencyDetector:
    """Detect whether a tool call references compressed content.

    The detector checks three dimensions:

    1. **File paths** — does the tool call reference a file that was compressed?
    2. **Line numbers** — does the tool call reference a specific line in a
       compressed file?
    3. **Key terms** — do the tool call parameters contain key terms from a
       compressed block?

    Usage::

        detector = DependencyDetector(config)
        hits = detector.detect(tool_call, active_anchors)
    """

    def __init__(self, config: DetectorConfig) -> None:
        self._cfg = config

    def detect(
        self,
        tool_call: ToolCall,
        active_anchors: list[AnchorRef],
    ) -> list[DependencyHit]:
        """Run all detectors and return unique hits."""
        hits: list[DependencyHit] = []
        seen_anchors: set[str] = set()

        # 1. File path matching
        paths = _find_file_paths(tool_call)
        for path, confidence in paths:
            for anchor in active_anchors:
                if anchor.anchor_id in seen_anchors:
                    continue
                if not anchor.source_path:
                    continue
                if self._path_matches(path, anchor.source_path):
                    if confidence >= self._cfg.min_confidence_file:
                        hits.append(
                            DependencyHit(
                                anchor_id=anchor.anchor_id,
                                confidence=confidence,
                                match_type="file_path",
                                context_request=f"Find references to: {path}",
                            )
                        )
                        seen_anchors.add(anchor.anchor_id)

        # 2. Line number matching
        line_refs = _find_line_numbers(tool_call)
        for line_num, line_end, confidence in line_refs:
            for anchor in active_anchors:
                if anchor.anchor_id in seen_anchors:
                    continue
                if not anchor.source_path:
                    continue
                if confidence >= self._cfg.min_confidence_line:
                    range_str = (
                        f"lines {line_num}-{line_end}"
                        if line_end
                        else f"line {line_num}"
                    )
                    hits.append(
                        DependencyHit(
                            anchor_id=anchor.anchor_id,
                            confidence=confidence,
                            match_type="line_number",
                            context_request=range_str,
                        )
                    )
                    seen_anchors.add(anchor.anchor_id)

        # 3. Key term matching
        term_hits = _find_key_term_matches(tool_call, active_anchors)
        for anchor_id, confidence in term_hits:
            if anchor_id in seen_anchors:
                continue
            if confidence >= self._cfg.min_confidence_term:
                hits.append(
                    DependencyHit(
                        anchor_id=anchor_id,
                        confidence=confidence,
                        match_type="key_term",
                        context_request="Find matching content",
                    )
                )
                seen_anchors.add(anchor_id)

        return hits

    @staticmethod
    def _path_matches(call_path: str, anchor_path: str) -> bool:
        """Check if a tool-call path references a compressed file path."""
        # Normalize
        cp = call_path.replace("\\", "/").strip("./")
        ap = anchor_path.replace("\\", "/").strip("./")

        if cp == ap:
            return True
        if ap.endswith(cp):
            return True
        if cp.endswith(ap):
            return True
        return False
