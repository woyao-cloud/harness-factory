"""PlannerAgent — breaks a user task into a structured plan with subtasks."""

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

PLANNER_SYSTEM_PROMPT = """\
You are a planning agent. Break the user's request into 3-8 clear subtasks.

Guidelines:
- Each subtask has a single focus and a clear deliverable
- Set depends_on for tasks that depend on other tasks
- Be specific and actionable
- Output ONLY the plan — no conversation, no extra text"""


class PlannerAgent(AgentBase):
    """Creates a structured plan from a user task.

    The Planner:
    1. Takes the user's task description
    2. May use web tools to research the topic first
    3. Produces a Plan with PlanItems in Markdown format
    4. Parses the Markdown into structured Plan objects
    """

    def __init__(
        self,
        llm: LLMProvider,
        tools: list[Any] | None = None,
        model: str = "deepseek-v4-flash:cloud",
        system_prompt: str | None = None,
    ) -> None:
        config = AgentConfig(
            role=AgentRole.PLANNER,
            system_prompt=system_prompt or PLANNER_SYSTEM_PROMPT,
            model=model,
            max_tool_rounds=10,
            security_default_decision="allow",
            security_require_approval=(),
        )
        super().__init__(config, llm, tools)

    async def run(self, context: AgentContext) -> AgentResult:
        """Create a plan from the user task in the context."""
        try:
            prompt_text = (
                f"## User Request\n\n{context.user_task}\n\n"
                f"## Instructions\n\n"
                f"Create a detailed plan with 3-8 subtasks. "
                f"Output ONLY the plan in this exact Markdown format "
                f"(no extra text, no conversation):\n\n"
                f"## Plan\n\n"
                f"**Goal**: Restate the user's goal here\n\n"
                f"**Context**: Context for the worker\n\n"
                f"### Task: task_1\n"
                f"Description of task 1\n"
                f"- Depends on: (none)\n\n"
                f"### Task: task_2\n"
                f"Description of task 2\n"
                f"- Depends on: task_1"
            )
            logger.info("Planner prompt sent to LLM")
            result = await self._pipeline.turn(UserInput(text=prompt_text))
            logger.info("Planner response (first 300): %s", result.text[:300])
            plan = self._parse_plan(result.text)
            logger.info(
                "Planner created plan with %d tasks for: %s",
                len(plan.items), context.user_task[:60],
            )
            return AgentResult(
                role=AgentRole.PLANNER,
                text=result.text,
                data={"plan": plan},
            )
        except Exception as exc:
            logger.error("Planner failed: %s", exc)
            return AgentResult(
                role=AgentRole.PLANNER,
                text="",
                data={
                    "plan": Plan(
                        goal=context.user_task,
                        context="Planner failed, using single implicit task",
                        items=(PlanItem(id="task_1", description=context.user_task),),
                        status=PlanStatus.PENDING,
                    )
                },
                error=str(exc),
            )

    @staticmethod
    def _parse_plan(text: str) -> Plan:
        """Parse Markdown plan from LLM output into a Plan object.

        Looks for a ``## Plan`` section with ``**Goal**:``, ``**Context**:``,
        and ``### Task: <id>`` subsections. Falls back to a single-task plan
        if no valid structure is found.
        """
        # Locate the Plan section (## but not ###)
        plan_section = re.search(
            r"##\s*Plan\s*(.*?)(?=\n##(?!#)|\Z)", text, re.DOTALL | re.IGNORECASE,
        )
        if not plan_section:
            logger.warning("No ## Plan section found in planner output, using fallback")
            logger.warning("Raw planner output (first 500 chars): %s", text[:500])
            # Use the raw text as goal (first non-empty line)
            fallback_goal = ""
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    fallback_goal = line[:200]
                    break
            return Plan(
                goal=fallback_goal or text[:200],
                context="",
                items=(PlanItem(id="task_1", description=text[:1000]),),
                status=PlanStatus.PENDING,
            )

        plan_content = plan_section.group(1)

        # Extract goal — **Goal**: ...  (until next bold heading or end)
        goal_match = re.search(
            r"\*\*Goal\*\*\s*:\s*(.*?)(?=\n\s*\*\*|$|\n\s*###)", plan_content, re.DOTALL | re.IGNORECASE
        )
        goal = goal_match.group(1).strip() if goal_match else ""

        # Extract context — **Context**: ...
        context_match = re.search(
            r"\*\*Context\*\*\s*:\s*(.*?)(?=\n\s*\*\*|$|\n\s*###)", plan_content, re.DOTALL | re.IGNORECASE
        )
        context = context_match.group(1).strip() if context_match else ""

        # Extract all tasks — ### Task: <id>
        items: list[PlanItem] = []
        task_pattern = re.compile(
            r"###\s*Task\s*:\s*(\S+)\s*(.*?)(?=\n\s*###|\Z)",
            re.DOTALL | re.IGNORECASE,
        )
        for match in task_pattern.finditer(plan_content):
            tid = match.group(1).strip()
            task_body = match.group(2).strip()

            # Extract description (everything before "- Depends on:")
            deps_match = re.search(
                r"- Depends on:\s*(.*)", task_body, re.IGNORECASE
            )
            if deps_match:
                description = task_body[:deps_match.start()].strip()
                depends_str = deps_match.group(1).strip()
            else:
                description = task_body
                depends_str = ""

            # Normalize "(none)" or "" to empty tuple
            depends: tuple[str, ...] = ()
            if depends_str and depends_str.lower() not in ("(none)", "none", ""):
                depends = tuple(d.strip() for d in depends_str.split(",") if d.strip())

            items.append(
                PlanItem(
                    id=tid,
                    description=description,
                    status=TaskStatus.PENDING,
                    depends_on=depends,
                )
            )

        if not items:
            logger.warning("No task sections found in plan, using fallback")
            return Plan(
                goal=goal or "",
                context=context,
                items=(PlanItem(id="task_1", description=text[:500]),),
                status=PlanStatus.PENDING,
            )

        return Plan(
            goal=goal,
            context=context,
            items=tuple(items),
            status=PlanStatus.PENDING,
        )
