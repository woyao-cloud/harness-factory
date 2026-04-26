"""Multi-agent orchestration — Planner, Worker, Reviewer.

Provides:
- AgentBase — abstract base for all agents
- PlannerAgent — breaks tasks into structured plans
- WorkerAgent — executes plan subtasks
- ReviewAgent — verifies completed work
- AgentCoordinator — state machine orchestrator
"""

from .base import AgentBase, AgentConfig
from .coordinator import AgentCoordinator, CoordinatorConfig
from .planner import PlannerAgent
from .reviewer import ReviewAgent
from .types import (
    AgentContext,
    AgentResult,
    AgentRole,
    OrchestratorState,
    Plan,
    PlanItem,
    PlanStatus,
    ReviewResult,
    ReviewVerdict,
    TaskStatus,
)
from .worker import WorkerAgent

__all__ = [
    "AgentBase",
    "AgentConfig",
    "AgentContext",
    "AgentCoordinator",
    "AgentResult",
    "AgentRole",
    "CoordinatorConfig",
    "OrchestratorState",
    "Plan",
    "PlanItem",
    "PlanStatus",
    "PlannerAgent",
    "ReviewAgent",
    "ReviewResult",
    "ReviewVerdict",
    "TaskStatus",
    "WorkerAgent",
]
