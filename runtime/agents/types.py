"""Data types for multi-agent orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import auto, Enum
from typing import Any


class AgentRole(Enum):
    PLANNER = auto()
    WORKER = auto()
    REVIEWER = auto()


class TaskStatus(Enum):
    PENDING = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    FAILED = auto()
    SKIPPED = auto()


class PlanStatus(Enum):
    PENDING = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    FAILED = auto()


class ReviewVerdict(Enum):
    PASS = auto()
    FAIL = auto()
    NEEDS_REVISION = auto()


class OrchestratorState(Enum):
    IDLE = auto()
    PLANNING = auto()
    EXECUTING = auto()
    REVIEWING = auto()
    REVISING = auto()
    COMPLETED = auto()
    FAILED = auto()


@dataclass(frozen=True)
class PlanItem:
    """A single subtask in a plan."""

    id: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    depends_on: tuple[str, ...] = ()
    output_refs: tuple[str, ...] = ()
    revision_feedback: str = ""

    def with_status(self, status: TaskStatus) -> PlanItem:
        return PlanItem(
            id=self.id,
            description=self.description,
            status=status,
            depends_on=self.depends_on,
            output_refs=self.output_refs,
            revision_feedback=self.revision_feedback,
        )

    def with_output_refs(self, refs: tuple[str, ...]) -> PlanItem:
        return PlanItem(
            id=self.id,
            description=self.description,
            status=self.status,
            depends_on=self.depends_on,
            output_refs=refs,
            revision_feedback=self.revision_feedback,
        )

    def with_revision_feedback(self, feedback: str) -> PlanItem:
        return PlanItem(
            id=self.id,
            description=self.description,
            status=self.status,
            depends_on=self.depends_on,
            output_refs=self.output_refs,
            revision_feedback=feedback,
        )


@dataclass(frozen=True)
class Plan:
    """Structured plan with goal, context, and subtasks."""

    goal: str = ""
    context: str = ""
    items: tuple[PlanItem, ...] = ()
    status: PlanStatus = PlanStatus.PENDING

    @property
    def pending_tasks(self) -> list[PlanItem]:
        return [t for t in self.items if t.status is TaskStatus.PENDING]

    @property
    def completed_count(self) -> int:
        return sum(1 for t in self.items if t.status is TaskStatus.COMPLETED)

    @property
    def total_count(self) -> int:
        return len(self.items)

    def with_item_status(self, item_id: str, status: TaskStatus) -> Plan:
        return Plan(
            goal=self.goal,
            context=self.context,
            items=tuple(
                item.with_status(status) if item.id == item_id else item
                for item in self.items
            ),
            status=self.status,
        )

    def with_items(self, items: tuple[PlanItem, ...]) -> Plan:
        return Plan(
            goal=self.goal,
            context=self.context,
            items=items,
            status=self.status,
        )

    def with_status(self, status: PlanStatus) -> Plan:
        return Plan(
            goal=self.goal,
            context=self.context,
            items=self.items,
            status=status,
        )


@dataclass(frozen=True)
class AgentResult:
    """Output from a single agent run."""

    role: AgentRole = AgentRole.WORKER
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class ReviewResult:
    """Verdict from the ReviewAgent."""

    verdict: ReviewVerdict = ReviewVerdict.PASS
    feedback: str = ""
    task_results: tuple[dict[str, str], ...] = ()

    @property
    def passed(self) -> bool:
        return self.verdict is ReviewVerdict.PASS


@dataclass(frozen=True)
class AgentContext:
    """Shared context passed between agents during orchestration."""

    user_task: str = ""
    plan: Plan = field(default_factory=Plan)
    plan_file: str = ""
    work_result: AgentResult = field(default_factory=AgentResult)
    review_result: ReviewResult = field(default_factory=ReviewResult)
    iteration: int = 0
    max_iterations: int = 3
    revision_feedback: str = ""

    def with_plan(self, plan: Plan) -> AgentContext:
        return AgentContext(
            user_task=self.user_task,
            plan=plan,
            plan_file=self.plan_file,
            work_result=self.work_result,
            review_result=self.review_result,
            iteration=self.iteration,
            max_iterations=self.max_iterations,
            revision_feedback=self.revision_feedback,
        )

    def with_plan_file(self, path: str) -> AgentContext:
        return AgentContext(
            user_task=self.user_task,
            plan=self.plan,
            plan_file=path,
            work_result=self.work_result,
            review_result=self.review_result,
            iteration=self.iteration,
            max_iterations=self.max_iterations,
            revision_feedback=self.revision_feedback,
        )

    def with_work_result(self, work_result: AgentResult) -> AgentContext:
        return AgentContext(
            user_task=self.user_task,
            plan=self.plan,
            plan_file=self.plan_file,
            work_result=work_result,
            review_result=self.review_result,
            iteration=self.iteration,
            max_iterations=self.max_iterations,
            revision_feedback=self.revision_feedback,
        )

    def with_review_result(self, review_result: ReviewResult) -> AgentContext:
        return AgentContext(
            user_task=self.user_task,
            plan=self.plan,
            plan_file=self.plan_file,
            work_result=self.work_result,
            review_result=review_result,
            iteration=self.iteration,
            max_iterations=self.max_iterations,
            revision_feedback=self.revision_feedback,
        )

    def with_iteration(self, iteration: int) -> AgentContext:
        return AgentContext(
            user_task=self.user_task,
            plan=self.plan,
            plan_file=self.plan_file,
            work_result=self.work_result,
            review_result=self.review_result,
            iteration=iteration,
            max_iterations=self.max_iterations,
            revision_feedback=self.revision_feedback,
        )

    def with_revision_feedback(self, feedback: str) -> AgentContext:
        return AgentContext(
            user_task=self.user_task,
            plan=self.plan,
            plan_file=self.plan_file,
            work_result=self.work_result,
            review_result=self.review_result,
            iteration=self.iteration,
            max_iterations=self.max_iterations,
            revision_feedback=feedback,
        )
