"""Selective, revisioned task planning for the agent loop.

The model decides whether a task needs a plan.  Once it opts in by calling
``update_plan``, this ledger validates every complete snapshot, persists it
before publishing it, and exposes a small completion guard to AgentLoop.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from .models import RunStatus

PLAN_MIN_STEPS = 2
PLAN_MAX_STEPS = 7
PLAN_STEP_MAX_CHARS = 240
PLAN_EXPLANATION_MAX_CHARS = 1000
PLAN_STATUSES = frozenset({"pending", "in_progress", "completed", "blocked"})
OPEN_PLAN_STATUSES = frozenset({"pending", "in_progress"})


class PlanValidationError(ValueError):
    """The proposed plan snapshot violates the public plan contract."""


@dataclass(frozen=True)
class PlanStep:
    step: str
    status: str

    def to_dict(self) -> Dict[str, str]:
        return {"step": self.step, "status": self.status}


@dataclass(frozen=True)
class PlanSnapshot:
    revision: int
    state: str
    explanation: str
    steps: Tuple[PlanStep, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "revision": self.revision,
            "state": self.state,
            "explanation": self.explanation,
            "steps": [item.to_dict() for item in self.steps],
        }


PersistPlan = Callable[[PlanSnapshot, int], None]
FinishPlan = Callable[[str], None]


class PlanLedger:
    """Thread-safe in-memory plan state with optional durable callbacks.

    ``persist`` receives the new immutable snapshot and the expected previous
    revision.  The in-memory state advances only after that callback succeeds,
    so a database failure becomes a retryable tool failure rather than a
    misleading plan event.
    """

    def __init__(
        self,
        *,
        persist: Optional[PersistPlan] = None,
        finish: Optional[FinishPlan] = None,
    ) -> None:
        self._persist = persist
        self._finish = finish
        self._snapshot: Optional[PlanSnapshot] = None
        self._lock = threading.RLock()

    @property
    def snapshot(self) -> Optional[PlanSnapshot]:
        with self._lock:
            return self._snapshot

    @property
    def active_step(self) -> Optional[str]:
        with self._lock:
            if self._snapshot is None:
                return None
            for item in self._snapshot.steps:
                if item.status == "in_progress":
                    return item.step
            return None

    @property
    def needs_completion_update(self) -> bool:
        with self._lock:
            return bool(
                self._snapshot
                and any(
                    item.status in OPEN_PLAN_STATUSES for item in self._snapshot.steps
                )
            )

    @property
    def completed_step_count(self) -> int:
        with self._lock:
            if self._snapshot is None:
                return 0
            return sum(item.status == "completed" for item in self._snapshot.steps)

    def update(self, explanation: str, steps: Sequence[Dict[str, str]]) -> PlanSnapshot:
        normalized_explanation, normalized_steps = validate_plan(explanation, steps)
        with self._lock:
            previous_revision = self._snapshot.revision if self._snapshot else 0
            if self._snapshot is None and not any(
                item.status in OPEN_PLAN_STATUSES for item in normalized_steps
            ):
                raise PlanValidationError(
                    "a new plan must start with one in_progress step"
                )
            if self._snapshot is not None:
                next_status = {
                    item.step.casefold(): item.status for item in normalized_steps
                }
                for item in self._snapshot.steps:
                    if item.status != "completed":
                        continue
                    if next_status.get(item.step.casefold()) != "completed":
                        raise PlanValidationError(
                            "completed plan steps cannot be removed or reopened"
                        )
            snapshot = PlanSnapshot(
                revision=previous_revision + 1,
                state="active",
                explanation=normalized_explanation,
                steps=normalized_steps,
            )
            if self._persist is not None:
                self._persist(snapshot, previous_revision)
            self._snapshot = snapshot
            return snapshot

    def finish(self, status: RunStatus) -> Optional[PlanSnapshot]:
        """Derive and persist the terminal plan state without failing the run."""
        with self._lock:
            current = self._snapshot
            if current is None:
                return None
            if status is RunStatus.INTERRUPTED:
                state = "interrupted"
            elif status is RunStatus.ERROR:
                state = "failed"
            elif any(item.status in OPEN_PLAN_STATUSES for item in current.steps):
                state = "incomplete"
            elif any(item.status == "blocked" for item in current.steps):
                state = "blocked"
            else:
                state = "completed"
            terminal = PlanSnapshot(
                revision=current.revision,
                state=state,
                explanation=current.explanation,
                steps=current.steps,
            )
            self._snapshot = terminal
            if self._finish is not None:
                self._finish(state)
            return terminal


def validate_plan(explanation: Any, raw_steps: Any) -> tuple[str, Tuple[PlanStep, ...]]:
    """Validate and normalize one full plan snapshot."""
    if explanation is None:
        explanation = ""
    if not isinstance(explanation, str):
        raise PlanValidationError("explanation must be a string")
    explanation = explanation.strip()
    if len(explanation) > PLAN_EXPLANATION_MAX_CHARS:
        raise PlanValidationError(
            f"explanation must be at most {PLAN_EXPLANATION_MAX_CHARS} characters"
        )
    if not isinstance(raw_steps, (list, tuple)):
        raise PlanValidationError("plan must be an array")
    if not PLAN_MIN_STEPS <= len(raw_steps) <= PLAN_MAX_STEPS:
        raise PlanValidationError(
            f"plan must contain {PLAN_MIN_STEPS} to {PLAN_MAX_STEPS} steps"
        )

    steps = []
    seen: set[str] = set()
    in_progress = 0
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict) or set(raw) != {"step", "status"}:
            raise PlanValidationError(
                f"plan[{index}] must contain exactly step and status"
            )
        step = raw.get("step")
        status = raw.get("status")
        if not isinstance(step, str) or not step.strip():
            raise PlanValidationError(f"plan[{index}].step must be a non-empty string")
        step = " ".join(step.split())
        if len(step) > PLAN_STEP_MAX_CHARS:
            raise PlanValidationError(
                f"plan[{index}].step must be at most {PLAN_STEP_MAX_CHARS} characters"
            )
        identity = step.casefold()
        if identity in seen:
            raise PlanValidationError("plan steps must be unique")
        seen.add(identity)
        if status not in PLAN_STATUSES:
            raise PlanValidationError(
                f"plan[{index}].status must be one of {sorted(PLAN_STATUSES)}"
            )
        if status == "in_progress":
            in_progress += 1
        steps.append(PlanStep(step=step, status=str(status)))

    has_open = any(item.status in OPEN_PLAN_STATUSES for item in steps)
    if in_progress > 1:
        raise PlanValidationError("at most one plan step may be in_progress")
    if has_open and in_progress != 1:
        raise PlanValidationError(
            "an active plan must have exactly one in_progress step"
        )
    return explanation, tuple(steps)
