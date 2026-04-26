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
You are a planning agent. Your job is to break down a user's request into a \
structured plan with clear, sequential subtasks.

## How to plan

1. Understand the user's goal thoroughly.
2. If you need more information to plan effectively, use the available tools \
(web search, fetch, read) to gather context first.
3. Break the goal into 3-8 clear, sequential subtasks. Each subtask should:
   - Be a single, focused action
   - Have a clear deliverable
   - Depend on previous subtasks only when necessary
4. Identify which subtasks can run in parallel (no dependency between them).
5. Output the plan in the following XML format:

<plan>
<goal>The user's original goal, restated clearly</goal>
<context>Any important context or constraints for the worker</context>
<task id="task_1" depends_on="">
  Description of the first subtask
</task>
<task id="task_2" depends_on="task_1">
  Description of the second subtask (depends on task_1)
</task>
</plan>

## Guidelines

- Be specific. Each task should produce a tangible output.
- Tasks should not overlap in scope.
- If dependencies exist, specify them. If not, leave depends_on empty.
- Provide context that helps the worker understand approach and expected output.
- The plan format is critical — the worker agent will parse it programmatically.
"""


class PlannerAgent(AgentBase):
    """Creates a structured plan from a user task.

    The Planner:
    1. Takes the user's task description
    2. May use web tools to research the topic first
    3. Produces a Plan with PlanItems in XML format
    4. Parses the XML into structured Plan objects
    """

    def __init__(
        self,
        llm: LLMProvider,
        tools: list[Any] | None = None,
        model: str = "deepseek-v4-flash:cloud",
    ) -> None:
        config = AgentConfig(
            role=AgentRole.PLANNER,
            system_prompt=PLANNER_SYSTEM_PROMPT,
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
                f"Analyze this request and create a detailed plan. "
                f"Use web search if you need to research the topic first, "
                f"then output your plan in XML format inside <plan> tags."
            )
            result = await self._pipeline.turn(UserInput(text=prompt_text))
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
        """Parse XML plan from LLM output into a Plan object.

        Falls back to a single-task plan if no valid XML is found.
        """
        # Extract <plan>...</plan> block
        plan_match = re.search(
            r"<plan>\s*(.*?)\s*</plan>", text, re.DOTALL | re.IGNORECASE
        )
        if not plan_match:
            logger.warning("No <plan> XML found in planner output, using fallback")
            return Plan(
                goal="",
                context="",
                items=(PlanItem(id="task_1", description=text[:500]),),
                status=PlanStatus.PENDING,
            )

        plan_content = plan_match.group(1)

        # Extract goal
        goal_match = re.search(
            r"<goal>(.*?)</goal>", plan_content, re.DOTALL | re.IGNORECASE
        )
        goal = goal_match.group(1).strip() if goal_match else ""

        # Extract context
        context_match = re.search(
            r"<context>(.*?)</context>", plan_content, re.DOTALL | re.IGNORECASE
        )
        context = context_match.group(1).strip() if context_match else ""

        # Extract all tasks (support both single and double quotes)
        items: list[PlanItem] = []
        task_pattern = re.compile(
            r"""<task\s+id=(["'])([^"']+)\1"""
            r"""\s*(?:depends_on=(["'])([^"']*)\3)?\s*>(.*?)</task>""",
            re.DOTALL | re.IGNORECASE,
        )
        for match in task_pattern.finditer(plan_content):
            tid = match.group(2).strip()
            depends_str = (match.group(4) or "").strip()
            description = match.group(5).strip()
            depends = tuple(
                d.strip() for d in depends_str.split(",") if d.strip()
            )
            items.append(
                PlanItem(
                    id=tid,
                    description=description,
                    status=TaskStatus.PENDING,
                    depends_on=depends,
                )
            )

        if not items:
            logger.warning("No <task> elements found in plan XML, using fallback")
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
