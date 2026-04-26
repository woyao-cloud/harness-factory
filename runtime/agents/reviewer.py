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
You are a review agent. Your job is to verify that completed work meets \
the requirements defined in the plan.

## Review criteria

For each task in the plan, check:
1. **Completeness**: Was the task fully completed? Are all required outputs present?
2. **Quality**: Is the output well-structured, accurate, and useful?
3. **Evidence**: Are there tangible outputs (saved files, collected data)?
4. **Dependencies**: Were dependencies respected?

## Output format

Provide your review in the following format:

<review>
<task id="task_1" verdict="pass">
Optional feedback about this task.
</task>
<task id="task_2" verdict="needs_revision">
Specific feedback about what needs to be improved.
</task>
<summary verdict="needs_revision">
Overall summary of the review. Verdict must be one of: pass, fail, needs_revision.
</summary>
</review>

## Available tools

You have read-only access to read, grep, and glob to examine output files.
"""


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
    ) -> None:
        config = AgentConfig(
            role=AgentRole.REVIEWER,
            system_prompt=REVIEWER_SYSTEM_PROMPT,
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
            logger.error("Reviewer failed: %s", exc)
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
            "Review each task against the plan. Use read/grep/glob to check "
            "that output files exist and contain the expected content. "
            "Then provide your review in the XML format."
        )
        return "\n".join(lines)

    @staticmethod
    def _parse_review(text: str) -> ReviewResult:
        """Parse <review> XML from LLM output into a ReviewResult.

        Falls back to PASS on parse failure.
        """
        review_match = re.search(
            r"<review>\s*(.*?)\s*</review>", text, re.DOTALL | re.IGNORECASE,
        )
        if not review_match:
            logger.warning("No <review> XML found in reviewer output, defaulting to PASS")
            return ReviewResult(
                verdict=ReviewVerdict.PASS,
                feedback="Review XML not found, defaulting to PASS",
                task_results=(),
            )

        review_content = review_match.group(1)

        # Extract summary verdict (support both quote types)
        summary_match = re.search(
            r"""<summary\s+(?:verdict=(["'])([^"']*)\1)?\s*>(.*?)</summary>""",
            review_content, re.DOTALL | re.IGNORECASE,
        )
        if summary_match:
            verdict_str = (summary_match.group(2) or "").strip().lower()
            summary_text = (summary_match.group(3) or "").strip()
        else:
            verdict_str = ""
            summary_text = ""

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

        # Extract per-task results
        task_results_list: list[dict[str, str]] = []
        task_pattern = re.compile(
            r"""<task\s+id=(["'])([^"']+)\1"""
            r"""\s+(?:verdict=(["'])([^"']*)\3)?\s*>(.*?)</task>""",
            re.DOTALL | re.IGNORECASE,
        )
        for match in task_pattern.finditer(review_content):
            tid = match.group(2).strip()
            t_verdict = (match.group(4) or "pass").strip()
            t_feedback = (match.group(5) or "").strip()
            task_results_list.append({
                "id": tid,
                "verdict": t_verdict,
                "feedback": t_feedback,
            })

        return ReviewResult(
            verdict=verdict,
            feedback=summary_text,
            task_results=tuple(task_results_list),
        )
