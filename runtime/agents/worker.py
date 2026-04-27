"""WorkerAgent — executes plan subtasks using domain tools."""

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
    Plan,
    PlanItem,
    PlanStatus,
    TaskStatus,
)

logger = logging.getLogger(__name__)

WORKER_SYSTEM_PROMPT = """\
You are a research worker. Execute the plan by searching, reading, and writing.

Available tools: web_search, web_fetch, search, read, write, edit, glob, grep

Complete each task in order, respecting dependencies. Use tools as needed.
When ALL tasks are done, output a ## Work Report section with each task's status."""


class WorkerAgent(AgentBase):
    """Executes plan subtasks using domain tools.

    The Worker receives the full plan and works through all subtasks
    autonomously via AutoPipeline. It saves outputs to the working
    directory and produces a work report upon completion.
    """

    def __init__(
        self,
        llm: LLMProvider,
        tools: list[Any] | None = None,
        model: str = "deepseek-v4-flash:cloud",
        system_prompt: str | None = None,
        instructions_override: str | None = None,
    ) -> None:
        self._instructions_override = instructions_override
        config = AgentConfig(
            role=AgentRole.WORKER,
            system_prompt=system_prompt or WORKER_SYSTEM_PROMPT,
            model=model,
            max_tool_rounds=25,
            security_default_decision="ask",
            security_require_approval=("write", "edit"),
        )
        super().__init__(config, llm, tools)

    async def run(self, context: AgentContext) -> AgentResult:
        """Execute the plan from the given context."""
        try:
            prompt = self._build_worker_prompt(context)
            logger.info("Worker prompt sent to LLM")
            result = await self._pipeline.turn(UserInput(text=prompt))
            logger.info("Worker response (first 300): %s", result.text[:300])
            report = self._parse_work_report(result.text)

            # Update task statuses based on report
            updated_plan = self._apply_report_to_plan(context.plan, report)
            report_text = result.text

            return AgentResult(
                role=AgentRole.WORKER,
                text=report_text,
                data={
                    "plan": updated_plan,
                    "work_report": report,
                },
            )
        except Exception as exc:
            logger.error("Worker failed: %s", exc)
            return AgentResult(
                role=AgentRole.WORKER,
                text="",
                data={
                    "plan": context.plan.with_status(PlanStatus.FAILED),
                    "work_report": {},
                },
                error=str(exc),
            )

    def _build_worker_prompt(self, context: AgentContext) -> str:
        """Build the prompt for the worker from the plan and context."""
        lines: list[str] = []

        if context.plan_file:
            # Plan is on disk — tell LLM to read it via tools
            lines.append("## Plan\n")
            lines.append(
                f"The plan has been saved to **{context.plan_file}**. "
                f"Use the **read** tool with `file_path=\"{context.plan_file}\"` "
                f"to see the full task list.\n"
            )
        else:
            # Fallback: embed plan directly (backward compatible)
            plan = context.plan
            lines.append(f"## Goal\n\n{plan.goal}")
            if plan.context:
                lines.append(f"\n## Context\n\n{plan.context}")
            lines.append("\n## Tasks\n")
            for item in plan.items:
                deps = f" (depends on: {', '.join(item.depends_on)})" if item.depends_on else ""
                lines.append(f"### {item.id}{deps}")
                lines.append(item.description)
                lines.append("")

        if context.revision_feedback:
            lines.append("\n## Revision Feedback\n")
            lines.append(
                "The previous execution was reviewed and needs revision. "
                "Please address the following:\n"
            )
            lines.append(context.revision_feedback)

        instructions = (
            self._instructions_override
            if self._instructions_override
            else "Work through each task in order. Use the available tools to "
                 "search for information, read files, and save your findings."
        )
        lines.append(
            f"\n## Instructions\n\n{instructions}\n\n"
            "When ALL tasks are done, output your work report in this exact "
            "Markdown format (no extra text after it):\n\n"
            "## Work Report\n\n"
            "### Task: task_1\n"
            "**Status**: completed\n"
            "Summary of what was done for task_1, including file paths.\n\n"
            "### Task: task_2\n"
            "**Status**: failed\n"
            "Explanation of why this task could not be completed."
        )
        return "\n".join(lines)

    @staticmethod
    def _parse_work_report(text: str) -> dict[str, str]:
        """Parse Markdown work report from LLM output into a dict of task_id -> status.

        Looks for a ``## Work Report`` section with ``### Task: <id>``
        subsections containing ``**Status**:``. Returns {task_id: status_text, ...}
        """
        # Locate the Work Report section (## but not ###)
        report_section = re.search(
            r"##\s*Work Report\s*(.*?)(?=\n##(?!#)|\Z)",
            text, re.DOTALL | re.IGNORECASE,
        )
        if not report_section:
            logger.warning("No ## Work Report section found in worker output")
            logger.warning("Raw worker output (first 500): %s", text[:500])
            return {}

        report_content = report_section.group(1)
        results: dict[str, str] = {}

        # Extract each task section: ### Task: <id> ... (until next ### or end)
        task_pattern = re.compile(
            r"###\s*Task\s*:\s*(\S+)\s*(.*?)(?=\n###(?!#)|\Z)",
            re.DOTALL | re.IGNORECASE,
        )
        for match in task_pattern.finditer(report_content):
            tid = match.group(1).strip()
            task_body = match.group(2).strip()

            # Extract status: **Status**: <value>
            status_match = re.search(
                r"\*\*Status\*\*\s*:\s*(\S+)", task_body, re.IGNORECASE
            )
            status = status_match.group(1).strip() if status_match else "completed"

            # Everything after the status line
            summary = task_body
            if status_match:
                # Remove the status line itself
                summary = task_body[status_match.end():].strip()

            results[tid] = f"[{status}] {summary}"

        return results

    @staticmethod
    def _apply_report_to_plan(
        plan: Plan, report: dict[str, str]
    ) -> Plan:
        """Apply work report statuses back to the plan."""
        if not report:
            return plan.with_status(PlanStatus.IN_PROGRESS)

        updated_items = []
        for item in plan.items:
            if item.id in report:
                status = TaskStatus.COMPLETED
                if "[failed]" in report[item.id].lower():
                    status = TaskStatus.FAILED
                updated_items.append(item.with_status(status))
            else:
                updated_items.append(item)

        all_done = all(
            t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            for t in updated_items
        )
        return Plan(
            goal=plan.goal,
            context=plan.context,
            items=tuple(updated_items),
            status=PlanStatus.COMPLETED if all_done else PlanStatus.IN_PROGRESS,
        )
