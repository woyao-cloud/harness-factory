"""Content restoration — resolving dependency hits against the layered store.

Responsibilities:
* Take ``DependencyHit`` list → query ``LayeredStore`` → produce ``RestoredSnippet``.
* Format snippets as supplemental context for prompt injection.
* Provide degradation fallback when content cannot be restored.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .dependency import DependencyDetector, DependencyHit
from .store import LayeredStore
from .types import RestoredSnippet


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RestorerConfig:
    """Tuning knobs for the restorer."""

    max_restore_tokens: int = 2000
    max_snippets_per_round: int = 3
    enable_degrade_reload: bool = True


# ── Restorer ─────────────────────────────────────────────────────────────────


class Restorer:
    """Resolve dependency hits into content for prompt injection.

    This is the read-side counterpart to ``AsyncCompressor``.  When a tool call
    references compressed content, the ``Restorer`` fetches the original data
    from ``LayeredStore`` and creates a ``supplemental_context`` block that the
    pipeline injects before the next LLM inference.
    """

    def __init__(self, config: RestorerConfig) -> None:
        self._cfg = config

    def restore(
        self,
        hits: list[DependencyHit],
        store: LayeredStore,
    ) -> list[RestoredSnippet]:
        """Resolve dependency hits to restored snippets.

        Snippets are ordered by descending confidence.  At most
        ``max_snippets_per_round`` are returned.
        """
        sorted_hits = sorted(hits, key=lambda h: h.confidence, reverse=True)
        snippets: list[RestoredSnippet] = []

        for hit in sorted_hits[: self._cfg.max_snippets_per_round]:
            snippet = store.restore_snippet(
                anchor_id=hit.anchor_id,
                query=hit.context_request,
                max_tokens=self._cfg.max_restore_tokens,
            )
            if snippet is not None:
                snippets.append(snippet)

        return snippets

    def format_supplemental_context(self, snippets: list[RestoredSnippet]) -> str:
        """Format restored snippets into a supplemental context block.

        The formatted block is designed to be injected between the system
        prompt and the latest user message — the pipeline places it
        immediately before the next LLM inference call.
        """
        if not snippets:
            return ""

        lines: list[str] = [
            "<supplemental_context>",
            "The following content was restored from compressed history"
            " — it is available for this inference round only:",
            "",
        ]

        for i, snippet in enumerate(snippets, start=1):
            lines.append(f"--- restored block {i} ---")
            lines.append(snippet.format_injection())
            lines.append("--- end block {i} ---")
            lines.append("")

        lines.append("</supplemental_context>")

        return "\n".join(lines)

    # ── Degradation fallback ─────────────────────────────────────────────

    def degrade(
        self,
        hit: DependencyHit,
        store: LayeredStore,
    ) -> RestoredSnippet | None:
        """Fallback strategy when primary restoration fails.

        Tries:
        1. Look up the anchor's ``source_path`` and note the path was
           compressed — caller can re-read from disk.
        2. Return the anchor summary so the LLM at least knows *something*
           was there.
        """
        anchor = store.read_anchor(hit.anchor_id)
        if anchor is None:
            return None

        if self._cfg.enable_degrade_reload and anchor.source_path:
            return RestoredSnippet(
                anchor_id=hit.anchor_id,
                content=(
                    f"[Content was previously compressed. "
                    f"Source file: {anchor.source_path} "
                    f"— re-read it from disk for full context]"
                ),
                source_path=anchor.source_path,
                total_original_tokens=0,
                restored_tokens=0,
            )

        return RestoredSnippet(
            anchor_id=hit.anchor_id,
            content=f"[Compressed content: {anchor.summary}]",
            total_original_tokens=0,
            restored_tokens=0,
        )
