"""``update_plan`` tool for opt-in, selective task planning."""

from __future__ import annotations

from typing import Dict

from ..planning import (
    PLAN_EXPLANATION_MAX_CHARS,
    PLAN_MAX_STEPS,
    PLAN_MIN_STEPS,
    PLAN_STEP_MAX_CHARS,
    PlanLedger,
    PlanValidationError,
    validate_plan,
)
from .base import PLAN_PERSIST_FAILED, ToolEffect, ToolExecutionError, ToolSpec

DESCRIPTION = (
    "Create or replace the current task plan when the task is genuinely complex. "
    "Do not call this tool for simple questions or obvious one-step changes. "
    "Send the full 2-7 step plan on every update. While work remains, keep "
    "exactly one step in_progress; never reopen a completed step."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "explanation": {
            "type": "string",
            "maxLength": PLAN_EXPLANATION_MAX_CHARS,
            "description": "Brief reason for creating or revising the plan.",
        },
        "plan": {
            "type": "array",
            "minItems": PLAN_MIN_STEPS,
            "maxItems": PLAN_MAX_STEPS,
            "items": {
                "type": "object",
                "properties": {
                    "step": {
                        "type": "string",
                        "maxLength": PLAN_STEP_MAX_CHARS,
                        "description": "One outcome-oriented task step.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "blocked"],
                    },
                },
                "required": ["step", "status"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["plan"],
    "additionalProperties": False,
}


def _validate(args: Dict) -> Dict:
    unknown = set(args) - {"explanation", "plan"}
    if unknown:
        raise ToolExecutionError.invalid_argument(
            f"unexpected update_plan arguments: {', '.join(sorted(unknown))}"
        )
    try:
        explanation, steps = validate_plan(
            args.get("explanation", ""), args.get("plan")
        )
    except PlanValidationError as exc:
        raise ToolExecutionError.invalid_argument(
            str(exc), "send the complete plan with exactly one active in_progress step"
        ) from exc
    return {
        "explanation": explanation,
        "plan": [item.to_dict() for item in steps],
    }


def _handle(args: Dict, ledger: PlanLedger) -> Dict:
    try:
        snapshot = ledger.update(args["explanation"], args["plan"])
    except PlanValidationError as exc:
        raise ToolExecutionError.invalid_argument(str(exc)) from exc
    except Exception as exc:
        raise ToolExecutionError(
            PLAN_PERSIST_FAILED,
            "plan update could not be persisted",
            retryable=True,
            recovery_hint="retry update_plan once; if it still fails, report the persistence failure",
        ) from exc
    return snapshot.to_dict()


def build_update_plan_spec(ledger: PlanLedger) -> ToolSpec:
    return ToolSpec(
        name="update_plan",
        description=DESCRIPTION,
        schema=SCHEMA,
        effect=ToolEffect.READ,
        validator=_validate,
        handler=lambda args: _handle(args, ledger),
    )
