"""Local schedule/poll trigger decision helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Callable

from appv24.workflows.activation import WorkflowActivationRecord, WorkflowActivationStore


@dataclass(frozen=True)
class TriggerTickDecision:
    state: str
    reason: str
    activation_id: str
    should_run: bool


@dataclass(frozen=True)
class TriggerRunDecision:
    activation_id: str
    trigger_type: str
    status: str
    reason: str
    should_run: bool
    run_id: str | None = None
    run_status: str | None = None


@dataclass(frozen=True)
class TriggerSweepReport:
    status: str
    checked: int
    ran: int
    skipped: int
    blocked: int
    decisions: tuple[TriggerRunDecision, ...]


@dataclass(frozen=True)
class TriggerLoopReport:
    status: str
    ticks: int
    interval_seconds: float
    ran: int
    skipped: int
    blocked: int
    sweeps: tuple[TriggerSweepReport, ...]


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


def run_due_activation_tick(
    store: WorkflowActivationStore,
    activation_id: str,
    *,
    now: datetime,
    running_count: int = 0,
) -> TriggerRunDecision:
    activation = store.get_activation(activation_id)
    tick = evaluate_trigger_tick(activation, now=now, running_count=running_count)
    if not tick.should_run:
        return TriggerRunDecision(
            activation_id=activation.activation_id,
            trigger_type=activation.trigger_type,
            status=tick.state,
            reason=tick.reason,
            should_run=False,
        )

    timestamp = now.isoformat()
    store.mark_run_started(activation.activation_id, started_at=timestamp)
    try:
        report = store.run_activation(activation.activation_id, finished_at=timestamp)
    except ValueError as error:
        return TriggerRunDecision(
            activation_id=activation.activation_id,
            trigger_type=activation.trigger_type,
            status="blocked",
            reason=str(error),
            should_run=True,
        )

    return TriggerRunDecision(
        activation_id=activation.activation_id,
        trigger_type=activation.trigger_type,
        status="ran",
        reason="trigger run finished",
        should_run=True,
        run_id=report.run_id,
        run_status=report.status,
    )


def run_due_activation_sweep(
    store: WorkflowActivationStore,
    *,
    now: datetime,
    limit: int | None = None,
) -> TriggerSweepReport:
    decisions: list[TriggerRunDecision] = []
    for index, activation in enumerate(store.list_activations()):
        if limit is not None and index >= max(0, limit):
            break
        decisions.append(run_due_activation_tick(store, activation.activation_id, now=now))

    ran = sum(1 for decision in decisions if decision.status == "ran")
    blocked = sum(1 for decision in decisions if decision.status == "blocked")
    skipped = len(decisions) - ran - blocked
    status = "blocked" if blocked else "ran" if ran else "idle"
    return TriggerSweepReport(
        status=status,
        checked=len(decisions),
        ran=ran,
        skipped=skipped,
        blocked=blocked,
        decisions=tuple(decisions),
    )


def format_trigger_sweep_report(report: TriggerSweepReport) -> str:
    lines = [
        f"Workflow activation sweep: {report.status}",
        f"checked: {report.checked}",
        f"ran: {report.ran}",
        f"skipped: {report.skipped}",
        f"blocked: {report.blocked}",
    ]
    for decision in report.decisions:
        suffix = ""
        if decision.run_id:
            suffix += f" run={decision.run_id}"
        if decision.run_status:
            suffix += f" run_status={decision.run_status}"
        lines.append(
            f"- {decision.activation_id} {decision.trigger_type} "
            f"{decision.status} {decision.reason}{suffix}"
        )
    return "\n".join(lines)


def run_activation_scheduler_loop(
    store: WorkflowActivationStore,
    *,
    ticks: int,
    interval_seconds: float,
    limit: int | None = None,
    now_fn: Callable[[], datetime] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> TriggerLoopReport:
    total_ticks = max(0, int(ticks))
    interval = max(0.0, float(interval_seconds))
    now = now_fn or (lambda: datetime.now(timezone.utc))
    sleep = sleep_fn or time.sleep
    sweeps: list[TriggerSweepReport] = []

    for index in range(total_ticks):
        sweeps.append(run_due_activation_sweep(store, now=now(), limit=limit))
        if index < total_ticks - 1 and interval > 0:
            sleep(interval)

    ran = sum(sweep.ran for sweep in sweeps)
    skipped = sum(sweep.skipped for sweep in sweeps)
    blocked = sum(sweep.blocked for sweep in sweeps)
    status = "blocked" if blocked else "ran" if ran else "idle"
    return TriggerLoopReport(
        status=status,
        ticks=total_ticks,
        interval_seconds=interval,
        ran=ran,
        skipped=skipped,
        blocked=blocked,
        sweeps=tuple(sweeps),
    )


def format_trigger_loop_report(report: TriggerLoopReport) -> str:
    lines = [
        f"Workflow activation loop: {report.status}",
        f"ticks: {report.ticks}",
        f"interval seconds: {report.interval_seconds:g}",
        f"ran: {report.ran}",
        f"skipped: {report.skipped}",
        f"blocked: {report.blocked}",
    ]
    for index, sweep in enumerate(report.sweeps, start=1):
        lines.append(
            f"tick {index}: {sweep.status} checked={sweep.checked} "
            f"ran={sweep.ran} skipped={sweep.skipped} blocked={sweep.blocked}"
        )
    return "\n".join(lines)


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
