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
You are a research worker agent. Your job is to execute a research plan \
by searching for papers, fetching content, reading files, and writing summaries.

## Available tools

- web_search — Search the web for information
- web_fetch — Fetch content from a URL
- read — Read a local file
- write — Write content to a local file
- edit — Edit a file by replacing text
- glob — List files matching a pattern
- grep — Search for text within files

## How to work

You will be given a plan with subtasks. Complete each subtask in order, \
respecting dependencies. Use the tools to search, read, write, and process data.

## Output format

When you finish all tasks, provide a summary of what was accomplished:

<work_report>
<task id="task_1" status="completed">
Summary of what was done for task_1, including file paths.
</task>
<task id="task_2" status="failed">
Explanation of why this task could not be completed.
</task>
</work_report>
"""


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
    ) -> None:
        config = AgentConfig(
            role=AgentRole.WORKER,
            system_prompt=WORKER_SYSTEM_PROMPT,
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
            result = await self._pipeline.turn(UserInput(text=prompt))
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
        plan = context.plan
        lines = [f"## Goal\n\n{plan.goal}"]
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

        lines.append(
            "\n## Instructions\n\n"
            "Work through each task in order. Use the available tools to "
            "search for information, read files, and save your findings. "
            "When finished, output a <work_report> with the status of each task."
        )
        return "\n".join(lines)

    @staticmethod
    def _parse_work_report(text: str) -> dict[str, str]:
        """Parse <work_report> XML from LLM output into a dict of task_id -> status.

        Returns {task_id: status_text, ...}
        """
        report_match = re.search(
            r"<work_report>\s*(.*?)\s*</work_report>",
            text, re.DOTALL | re.IGNORECASE,
        )
        if not report_match:
            logger.warning("No <work_report> XML found in worker output")
            return {}

        report_content = report_match.group(1)
        results: dict[str, str] = {}
        task_pattern = re.compile(
            r"""<task\s+id=(["'])([^"']+)\1"""
            r"""\s*(?:status=(["'])([^"']*)\3)?\s*>(.*?)</task>""",
            re.DOTALL | re.IGNORECASE,
        )
        for match in task_pattern.finditer(report_content):
            tid = match.group(2).strip()
            status = (match.group(4) or "completed").strip()
            summary = match.group(5).strip()
            results[tid] = f"[{status}] {summary}"

        return results

    @staticmethod
    def _apply_report_to_plan(
        plan: Plan, report: dict[str, str]
    ) -> Plan:
        """Apply work report statuses back to the plan."""
        if not report:
            return plan.with_status(PlanStatus.COMPLETED)

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
