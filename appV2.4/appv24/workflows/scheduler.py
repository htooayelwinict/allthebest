"""Local schedule/poll trigger decision helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from appv24.workflows.activation import WorkflowActivationRecord


@dataclass(frozen=True)
class TriggerTickDecision:
    state: str
    reason: str
    activation_id: str
    should_run: bool


def evaluate_trigger_tick(
    activation: WorkflowActivationRecord,
    *,
    now: datetime,
    running_count: int = 0,
) -> TriggerTickDecision:
    if not activation.enabled:
        return _decision("disabled", "activation disabled", activation, should_run=False)
    if activation.trigger_type not in {"schedule", "poll"}:
        return _decision("skipped", f"trigger type is not automatic: {activation.trigger_type}", activation, should_run=False)
    if running_count >= activation.max_concurrency:
        return _decision("skipped", "max concurrency reached", activation, should_run=False)
    if _cooldown_active(activation, now=now):
        return _decision("skipped", "cooldown active", activation, should_run=False)
    return _decision("due", "trigger due", activation, should_run=True)


def _cooldown_active(activation: WorkflowActivationRecord, *, now: datetime) -> bool:
    if activation.cooldown_seconds <= 0 or activation.last_finished_at is None:
        return False
    finished_at = _parse_datetime(activation.last_finished_at)
    if finished_at is None:
        return False
    return (now - finished_at).total_seconds() < activation.cooldown_seconds


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _decision(state: str, reason: str, activation: WorkflowActivationRecord, *, should_run: bool) -> TriggerTickDecision:
    return TriggerTickDecision(
        state=state,
        reason=reason,
        activation_id=activation.activation_id,
        should_run=should_run,
    )
