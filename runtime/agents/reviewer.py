"""ReviewAgent — verifies completed work against the plan."""

from __future__ import annotations

import logging
import re
from typing import Any

from runtime.llm_provider import LLMProvider
from runtime.types import UserInput

from .base import AgentBase, AgentConfig
from .types import (
    AgentContext,
    AgentResult,
    AgentRole,
    ReviewResult,
    ReviewVerdict,
)

logger = logging.getLogger(__name__)

REVIEWER_SYSTEM_PROMPT = """\
You are a review agent. Verify completed work against the plan.

Criteria: completeness, quality, evidence, dependencies.

Review the work result text below. Output ## Review with \
**Verdict**: (PASS/FAIL/NEEDS_REVISION), **Feedback**:, and per-task results."""


class ReviewAgent(AgentBase):
    """Verifies completed work against the plan.

    The ReviewAgent:
    1. Receives the plan and the worker's results
    2. Checks each task for completeness, quality, and evidence
    3. Produces a structured ReviewResult with pass/fail/needs_revision verdict
    """

    def __init__(
        self,
        llm: LLMProvider,
        tools: list[Any] | None = None,
        model: str = "deepseek-v4-flash:cloud",
        system_prompt: str | None = None,
    ) -> None:
        config = AgentConfig(
            role=AgentRole.REVIEWER,
            system_prompt=system_prompt or REVIEWER_SYSTEM_PROMPT,
            model=model,
            max_tool_rounds=10,
            security_default_decision="allow",
            security_require_approval=(),
        )
        super().__init__(config, llm, tools)

    async def run(self, context: AgentContext) -> AgentResult:
        """Review the completed work against the plan."""
        try:
            prompt = self._build_review_prompt(context)
            result = await self._pipeline.turn(UserInput(text=prompt))
            review = self._parse_review(result.text)

            return AgentResult(
                role=AgentRole.REVIEWER,
                text=result.text,
                data={"review": review},
            )
        except Exception as exc:
            logger.warning("Reviewer LLM call failed, defaulting to PASS: %s", exc)
            return AgentResult(
                role=AgentRole.REVIEWER,
                text="",
                data={
                    "review": ReviewResult(
                        verdict=ReviewVerdict.PASS,
                        feedback=f"Reviewer error, defaulting to PASS: {exc}",
                        task_results=(),
                    )
                },
                error=str(exc),
            )

    def _build_review_prompt(self, context: AgentContext) -> str:
        """Build the review prompt from plan and work results."""
        plan = context.plan
        lines = [
            f"## Goal\n\n{plan.goal}",
            "\n## Plan\n",
        ]
        for item in plan.items:
            lines.append(f"- **{item.id}**: {item.description} (status: {item.status.name})")

        lines.append("\n## Completed Work\n")
        if context.work_result.text:
            lines.append(context.work_result.text[:5000])
        else:
            lines.append("(No work output available)")

        lines.append(
            "\n## Instructions\n\n"
            "Review each task against the plan by analyzing the work result text. "
            "Then provide your review in the Markdown format described above."
        )
        return "\n".join(lines)

    @staticmethod
    def _parse_review(text: str) -> ReviewResult:
        """Parse Markdown review from LLM output into a ReviewResult.

        Looks for a ``## Review`` section with ``**Verdict**:``,
        ``**Feedback**:``, and ``### Task: <id>`` subsections.
        Falls back to PASS on parse failure.
        """
        # Locate the Review section (## but not ###)
        review_section = re.search(
            r"##\s*Review\s*(.*?)(?=\n##(?!#)|\Z)",
            text, re.DOTALL | re.IGNORECASE,
        )
        if not review_section:
            logger.warning("No ## Review section found in reviewer output, defaulting to PASS")
            return ReviewResult(
                verdict=ReviewVerdict.PASS,
                feedback="Review section not found, defaulting to PASS",
                task_results=(),
            )

        review_content = review_section.group(1)

        # Extract overall verdict — **Verdict**: <value>
        verdict_match = re.search(
            r"\*\*Verdict\*\*\s*:\s*(\S+)", review_content, re.IGNORECASE
        )
        verdict_str = verdict_match.group(1).strip().lower() if verdict_match else ""

        # Map verdict string to enum
        if verdict_str in ("pass", "passed"):
            verdict = ReviewVerdict.PASS
        elif verdict_str in ("fail", "failed"):
            verdict = ReviewVerdict.FAIL
        elif verdict_str in ("needs_revision", "revision"):
            verdict = ReviewVerdict.NEEDS_REVISION
        else:
            logger.warning(
                "Unknown review verdict '%s', defaulting to PASS", verdict_str
            )
            verdict = ReviewVerdict.PASS

        # Extract feedback — **Feedback**: <text>
        feedback_match = re.search(
            r"\*\*Feedback\*\*\s*:\s*(.*?)(?=\n\s*\*\*(?!\*)|\n\s*###(?!#)|\Z)",
            review_content, re.DOTALL | re.IGNORECASE,
        )
        feedback = feedback_match.group(1).strip() if feedback_match else ""

        # Extract per-task results
        task_results_list: list[dict[str, str]] = []
        task_pattern = re.compile(
            r"###\s*Task\s*:\s*(\S+)\s*(.*?)(?=\n###(?!#)|\Z)",
            re.DOTALL | re.IGNORECASE,
        )
        for match in task_pattern.finditer(review_content):
            tid = match.group(1).strip()
            task_body = match.group(2).strip()

            # Extract task verdict
            t_verdict_match = re.search(
                r"\*\*Verdict\*\*\s*:\s*(\S+)", task_body, re.IGNORECASE
            )
            t_verdict = t_verdict_match.group(1).strip().lower() if t_verdict_match else "pass"

            # Extract task feedback
            t_feedback_match = re.search(
                r"\*\*Feedback\*\*\s*:\s*(.*)", task_body, re.DOTALL | re.IGNORECASE
            )
            t_feedback = t_feedback_match.group(1).strip() if t_feedback_match else ""

            task_results_list.append({
                "id": tid,
                "verdict": t_verdict,
                "feedback": t_feedback,
            })

        return ReviewResult(
            verdict=verdict,
            feedback=feedback,
            task_results=tuple(task_results_list),
        )
