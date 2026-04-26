"""AgentCoordinator — orchestrates the Planner → Worker → Review flow.

State machine::

    IDLE → PLANNING → EXECUTING → REVIEWING → COMPLETED
                      ↑            │
                      └──(revision)─┘  (max_iterations limit)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .planner import PlannerAgent
from .reviewer import ReviewAgent
from .types import (
    AgentContext,
    AgentResult,
    AgentRole,
    OrchestratorState,
    Plan,
    PlanStatus,
    ReviewResult,
    ReviewVerdict,
)
from .worker import WorkerAgent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoordinatorConfig:
    max_iterations: int = 3
    verbose: bool = False


class AgentCoordinator:
    """State machine that orchestrates Planner → Worker → Review.

    Usage::

        coord = AgentCoordinator(planner, worker, reviewer)
        result = await coord.run("Research topic X")
        print(result.text)
    """

    def __init__(
        self,
        planner: PlannerAgent,
        worker: WorkerAgent,
        reviewer: ReviewAgent,
        config: CoordinatorConfig | None = None,
    ) -> None:
        self._planner = planner
        self._worker = worker
        self._reviewer = reviewer
        self._cfg = config or CoordinatorConfig()
        self._state = OrchestratorState.IDLE
        self._context = AgentContext(max_iterations=self._cfg.max_iterations)

    @property
    def state(self) -> OrchestratorState:
        return self._state

    @property
    def context(self) -> AgentContext:
        return self._context

    async def run(
        self,
        user_task: str,
        confirm_plan: Callable[[Plan], bool] | None = None,
    ) -> AgentResult:
        """Run the full Planner → Worker → Review cycle.

        Args:
            user_task: The user's task description.
            confirm_plan: Optional callback. Called with the plan after
                planning. Return ``True`` to proceed, ``False`` to cancel.

        Returns:
            An AgentResult with the final output (from the worker or reviewer).
        """
        self._context = AgentContext(
            user_task=user_task,
            max_iterations=self._cfg.max_iterations,
        )

        # Phase 1: Plan
        self._state = OrchestratorState.PLANNING
        logger.warning("Coordinator: planning phase for task: %s", user_task[:80])
        plan_result = await self._planner.run(self._context)
        if not plan_result.success:
            self._state = OrchestratorState.FAILED
            logger.error("Coordinator: planner failed: %s", plan_result.error)
            return plan_result

        plan = plan_result.data.get("plan")
        if plan is None or not plan.items:
            self._state = OrchestratorState.FAILED
            return AgentResult(
                role=AgentRole.PLANNER,
                text="",
                error="Planner returned no tasks",
            )

        self._context = self._context.with_plan(plan)
        if self._cfg.verbose:
            logger.info(
                "Coordinator: plan created with %d tasks",
                len(plan.items),
            )

        # Phase 1.5: User confirmation (optional)
        if confirm_plan is not None:
            if not confirm_plan(plan):
                self._state = OrchestratorState.FAILED
                logger.warning("Coordinator: plan cancelled by user")
                return AgentResult(
                    role=AgentRole.PLANNER,
                    text=plan_result.text,
                    data={"plan": plan},
                    error="Plan cancelled by user",
                )

        # Phase 2-3: Execute + Review (with iteration)
        iteration = 0
        while iteration < self._cfg.max_iterations:
            self._context = self._context.with_iteration(iteration)

            # Phase 2: Execute
            self._state = OrchestratorState.EXECUTING
            logger.info(
                "Coordinator: execution phase (iteration %d/%d)",
                iteration + 1, self._cfg.max_iterations,
            )
            work_result = await self._worker.run(self._context)
            self._context = self._context.with_work_result(work_result)

            if not work_result.success:
                logger.error(
                    "Coordinator: worker failed on iteration %d: %s",
                    iteration, work_result.error,
                )

            # Phase 3: Review
            self._state = OrchestratorState.REVIEWING
            logger.info(
                "Coordinator: review phase (iteration %d/%d)",
                iteration + 1, self._cfg.max_iterations,
            )
            review_result = await self._reviewer.run(self._context)
            self._context = self._context.with_review_result(
                review_result.data.get("review", ReviewResult())
            )

            review: ReviewResult = self._context.review_result

            if self._cfg.verbose:
                logger.info(
                    "Coordinator: review verdict = %s",
                    review.verdict.name,
                )

            if review.passed:
                self._state = OrchestratorState.COMPLETED
                logger.info("Coordinator: review PASSED, completed")
                return AgentResult(
                    role=AgentRole.WORKER,
                    text=work_result.text,
                    data={
                        "plan": plan,
                        "review": review,
                        "iterations": iteration + 1,
                    },
                )

            # Review failed — prepare for revision
            iteration += 1
            if iteration >= self._cfg.max_iterations:
                self._state = OrchestratorState.COMPLETED
                logger.info(
                    "Coordinator: max iterations reached, returning partial results"
                )
                return AgentResult(
                    role=AgentRole.WORKER,
                    text=work_result.text,
                    data={
                        "plan": plan,
                        "review": review,
                        "iterations": iteration,
                        "partial": True,
                    },
                )

            self._state = OrchestratorState.REVISING
            self._context = self._context.with_revision_feedback(review.feedback)
            # Reset agent sessions so they can re-run with fresh context
            self._worker.reset()
            self._reviewer.reset()
            logger.info(
                "Coordinator: revising based on feedback (iteration %d)",
                iteration,
            )

        self._state = OrchestratorState.COMPLETED
        return AgentResult(
            role=AgentRole.WORKER,
            text=self._context.work_result.text if self._context.work_result else "",
            data={
                "plan": plan,
                "iterations": iteration,
            },
        )
