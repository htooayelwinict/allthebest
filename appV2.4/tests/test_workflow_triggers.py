from __future__ import annotations

from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import queue
import threading

import yaml

from appv24.workflows.activation import WorkflowActivationStore
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.scheduler import (
    evaluate_trigger_tick,
    format_trigger_loop_report,
    format_trigger_sweep_report,
    run_activation_scheduler_loop,
    run_due_activation_sweep,
    run_due_activation_tick,
)
from appv24.workflows.webhook_server import WorkflowWebhookServerConfig, serve_workflow_webhooks
from appv24.workflows.webhook_ingress import WebhookReplayStore, handle_local_webhook, handle_local_webhook_run


def test_activation_persists_trigger_runtime_state_defaults(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")

    activation = store.create_activation(
        package,
        trigger_type="schedule",
        trigger_config={"cron": "*/5 * * * *"},
        approve_all=True,
    )

    persisted = store.get_activation(activation.activation_id)
    assert persisted.trigger_config == {"cron": "*/5 * * * *"}
    assert persisted.cursor is None
    assert persisted.max_concurrency == 1
    assert persisted.cooldown_seconds == 0
    assert persisted.last_started_at is None
    assert persisted.last_finished_at is None
    assert persisted.consecutive_failures == 0


def test_scheduler_tick_blocks_disabled_running_and_cooldown_states(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(
        package,
        trigger_type="schedule",
        trigger_config={"cron": "*/5 * * * *"},
        cooldown_seconds=60,
        approve_all=True,
    )
    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)

    assert evaluate_trigger_tick(activation, now=now).state == "due"
    assert evaluate_trigger_tick(store.set_enabled(activation.activation_id, False), now=now).state == "disabled"

    store.set_enabled(activation.activation_id, True)
    running = store.mark_run_started(activation.activation_id, started_at="2026-07-07T11:59:30+00:00")
    skipped = evaluate_trigger_tick(running, now=now, running_count=1)
    assert skipped.state == "skipped"
    assert skipped.reason == "max concurrency reached"

    finished = store.mark_run_finished(activation.activation_id, run_id="run-1", status="passed", finished_at="2026-07-07T11:59:30+00:00")
    cooldown = evaluate_trigger_tick(finished, now=now)
    assert cooldown.state == "skipped"
    assert cooldown.reason == "cooldown active"


def test_scheduler_due_tick_runs_activation_and_records_runtime_state(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(
        package,
        trigger_type="schedule",
        trigger_config={"cron": "*/5 * * * *"},
        cooldown_seconds=60,
        approve_all=True,
    )
    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)

    decision = run_due_activation_tick(store, activation.activation_id, now=now)

    updated = store.get_activation(activation.activation_id)
    assert decision.status == "ran"
    assert decision.reason == "trigger run finished"
    assert decision.should_run is True
    assert decision.run_id == updated.last_run_id
    assert decision.run_status == "passed"
    assert updated.last_started_at == "2026-07-07T12:00:00+00:00"
    assert updated.last_finished_at == "2026-07-07T12:00:00+00:00"
    assert updated.last_status == "passed"
    assert updated.consecutive_failures == 0
    assert store.history(activation.activation_id) == (decision.run_id,)
    assert evaluate_trigger_tick(updated, now=now).state == "skipped"


def test_scheduler_skipped_tick_does_not_create_activation_run(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(package, trigger_type="schedule", enabled=False, approve_all=True)
    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)

    decision = run_due_activation_tick(store, activation.activation_id, now=now)

    assert decision.status == "disabled"
    assert decision.reason == "activation disabled"
    assert decision.should_run is False
    assert decision.run_id is None
    assert store.history(activation.activation_id) == ()
    assert not (store.root / activation.activation_id / "runs").exists()


def test_scheduler_sweep_runs_due_automatic_activations_only(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    manual = store.create_activation(package, trigger_type="manual", approve_all=True)
    schedule = store.create_activation(package, trigger_type="schedule", approve_all=True)
    poll = store.create_activation(package, trigger_type="poll", approve_all=True)
    disabled = store.create_activation(package, trigger_type="schedule", enabled=False, approve_all=True)
    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)

    report = run_due_activation_sweep(store, now=now)

    assert report.status == "ran"
    assert report.checked == 4
    assert report.ran == 2
    assert report.skipped == 2
    assert report.blocked == 0
    decisions = {decision.activation_id: decision for decision in report.decisions}
    assert set(decisions) == {
        disabled.activation_id,
        manual.activation_id,
        poll.activation_id,
        schedule.activation_id,
    }
    assert decisions[schedule.activation_id].status == "ran"
    assert decisions[poll.activation_id].status == "ran"
    assert decisions[manual.activation_id].status == "skipped"
    assert decisions[disabled.activation_id].status == "disabled"
    assert store.history(schedule.activation_id) == (store.get_activation(schedule.activation_id).last_run_id,)
    assert store.history(poll.activation_id) == (store.get_activation(poll.activation_id).last_run_id,)
    assert store.history(manual.activation_id) == ()
    assert store.history(disabled.activation_id) == ()


def test_scheduler_sweep_report_is_operator_readable(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    schedule = store.create_activation(package, trigger_type="schedule", approve_all=True)
    manual = store.create_activation(package, trigger_type="manual", approve_all=True)
    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)

    text = format_trigger_sweep_report(run_due_activation_sweep(store, now=now))

    assert "Workflow activation sweep: ran" in text
    assert "checked: 2" in text
    assert "ran: 1" in text
    assert f"- {schedule.activation_id} schedule ran trigger run finished" in text
    assert f"- {manual.activation_id} manual skipped trigger type is not automatic: manual" in text


def test_scheduler_loop_runs_bounded_sweeps_without_sleep_after_last_tick(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(
        package,
        trigger_type="schedule",
        cooldown_seconds=60,
        approve_all=True,
    )
    now_values = iter(
        [
            datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 7, 12, 0, 30, tzinfo=timezone.utc),
        ]
    )
    sleeps: list[float] = []

    report = run_activation_scheduler_loop(
        store,
        ticks=2,
        interval_seconds=5,
        now_fn=lambda: next(now_values),
        sleep_fn=sleeps.append,
    )

    assert report.status == "ran"
    assert report.ticks == 2
    assert report.ran == 1
    assert report.skipped == 1
    assert report.blocked == 0
    assert [sweep.status for sweep in report.sweeps] == ["ran", "idle"]
    assert sleeps == [5]
    assert store.history(activation.activation_id) == (store.get_activation(activation.activation_id).last_run_id,)


def test_scheduler_loop_report_is_operator_readable(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    store.create_activation(package, trigger_type="schedule", approve_all=True)

    report = run_activation_scheduler_loop(
        store,
        ticks=1,
        interval_seconds=0,
        now_fn=lambda: datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc),
        sleep_fn=lambda seconds: None,
    )

    text = format_trigger_loop_report(report)
    assert "Workflow activation loop: ran" in text
    assert "ticks: 1" in text
    assert "ran: 1" in text
    assert "tick 1: ran checked=1 ran=1 skipped=0 blocked=0" in text


def test_local_webhook_accepts_once_then_blocks_replay(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(package, trigger_type="webhook", approve_all=True)
    replay_store = WebhookReplayStore(tmp_path / ".appv24" / "webhook-replays")

    accepted = handle_local_webhook(
        activation,
        payload=b'{"message":"hello"}',
        headers={"x-appv24-webhook-secret": "local-secret", "x-appv24-replay-key": "delivery-1"},
        shared_secret="local-secret",
        replay_store=replay_store,
    )
    replayed = handle_local_webhook(
        activation,
        payload=b'{"message":"hello"}',
        headers={"x-appv24-webhook-secret": "local-secret", "x-appv24-replay-key": "delivery-1"},
        shared_secret="local-secret",
        replay_store=replay_store,
    )

    assert accepted.status == "accepted"
    assert accepted.replay_key == "delivery-1"
    assert replayed.status == "blocked"
    assert replayed.reason == "duplicate webhook replay key"


def test_local_webhook_blocks_wrong_secret_and_non_webhook_activation(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    webhook = store.create_activation(package, trigger_type="webhook", approve_all=True)
    manual = store.create_activation(package, trigger_type="manual", approve_all=True)
    replay_store = WebhookReplayStore(tmp_path / ".appv24" / "webhook-replays")

    wrong_secret = handle_local_webhook(
        webhook,
        payload=b"{}",
        headers={"x-appv24-webhook-secret": "wrong", "x-appv24-replay-key": "delivery-2"},
        shared_secret="local-secret",
        replay_store=replay_store,
    )
    wrong_trigger = handle_local_webhook(
        manual,
        payload=b"{}",
        headers={"x-appv24-webhook-secret": "local-secret", "x-appv24-replay-key": "delivery-3"},
        shared_secret="local-secret",
        replay_store=replay_store,
    )

    assert wrong_secret.status == "blocked"
    assert wrong_secret.reason == "webhook secret mismatch"
    assert wrong_trigger.status == "blocked"
    assert wrong_trigger.reason == "activation is not webhook-triggered"


def test_local_webhook_run_accepts_event_and_runs_activation_once(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(package, trigger_type="webhook", approve_all=True)
    replay_store = WebhookReplayStore(tmp_path / ".appv24" / "webhook-replays")
    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)

    accepted = handle_local_webhook_run(
        store,
        activation.activation_id,
        payload=b'{"message":"hello"}',
        headers={"x-appv24-webhook-secret": "local-secret", "x-appv24-replay-key": "delivery-1"},
        shared_secret="local-secret",
        replay_store=replay_store,
        now=now,
    )
    replayed = handle_local_webhook_run(
        store,
        activation.activation_id,
        payload=b'{"message":"hello"}',
        headers={"x-appv24-webhook-secret": "local-secret", "x-appv24-replay-key": "delivery-1"},
        shared_secret="local-secret",
        replay_store=replay_store,
        now=now,
    )

    updated = store.get_activation(activation.activation_id)
    assert accepted.status == "ran"
    assert accepted.reason == "webhook run finished"
    assert accepted.replay_key == "delivery-1"
    assert accepted.run_id == updated.last_run_id
    assert accepted.run_status == "passed"
    assert replayed.status == "blocked"
    assert replayed.reason == "duplicate webhook replay key"
    assert replayed.run_id is None
    assert store.history(activation.activation_id) == (accepted.run_id,)
    assert updated.last_started_at == "2026-07-07T12:00:00+00:00"
    assert updated.last_finished_at == "2026-07-07T12:00:00+00:00"


def test_local_webhook_http_server_accepts_post_and_runs_activation(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(package, trigger_type="webhook", approve_all=True)

    status_code, payload, report = _serve_one_webhook_request(
        WorkflowWebhookServerConfig(
            activation_root=store.root,
            replay_root=tmp_path / ".appv24" / "webhook-replays",
            shared_secret="local-secret",
            port=0,
        ),
        path=f"/workflow-webhook/{activation.activation_id}",
        body=b'{"message":"hello"}',
        headers={"x-appv24-webhook-secret": "local-secret", "x-appv24-replay-key": "delivery-1"},
    )

    updated = store.get_activation(activation.activation_id)
    assert status_code == 200
    assert payload["status"] == "ran"
    assert payload["activation_id"] == activation.activation_id
    assert payload["run_status"] == "passed"
    assert report.status == "ran"
    assert report.handled == 1
    assert report.ran == 1
    assert report.blocked == 0
    assert store.history(activation.activation_id) == (updated.last_run_id,)


def test_local_webhook_http_server_blocks_wrong_secret_without_run(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(package, trigger_type="webhook", approve_all=True)

    status_code, payload, report = _serve_one_webhook_request(
        WorkflowWebhookServerConfig(
            activation_root=store.root,
            replay_root=tmp_path / ".appv24" / "webhook-replays",
            shared_secret="local-secret",
            port=0,
        ),
        path=f"/workflow-webhook/{activation.activation_id}",
        body=b"{}",
        headers={"x-appv24-webhook-secret": "wrong", "x-appv24-replay-key": "delivery-2"},
    )

    assert status_code == 403
    assert payload["status"] == "blocked"
    assert payload["reason"] == "webhook secret mismatch"
    assert report.status == "blocked"
    assert report.handled == 1
    assert report.ran == 0
    assert report.blocked == 1
    assert store.history(activation.activation_id) == ()


def _serve_one_webhook_request(
    config: WorkflowWebhookServerConfig,
    *,
    path: str,
    body: bytes,
    headers: dict[str, str],
) -> tuple[int, dict[str, object], object]:
    ready: queue.Queue[tuple[str, int]] = queue.Queue()
    result: queue.Queue[object] = queue.Queue()

    thread = threading.Thread(
        target=lambda: result.put(
            serve_workflow_webhooks(
                config,
                max_requests=1,
                on_ready=lambda host, port: ready.put((host, port)),
                now_fn=lambda: datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc),
            )
        ),
        daemon=True,
    )
    thread.start()
    host, port = ready.get(timeout=5)
    connection = http.client.HTTPConnection(host, port, timeout=5)
    connection.request("POST", path, body=body, headers=headers)
    response = connection.getresponse()
    response_body = response.read()
    connection.close()
    thread.join(timeout=5)
    assert not thread.is_alive()
    return response.status, json.loads(response_body.decode("utf-8")), result.get(timeout=5)


def _write_package(root: Path, example_name: str) -> Path:
    example = golden_workflow_examples()[example_name]
    package = root / example_name
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(yaml.safe_dump(dict(example.documents.workflow), sort_keys=False), encoding="utf-8")
    (package / "connectors.yaml").write_text(yaml.safe_dump(dict(example.documents.connectors), sort_keys=False), encoding="utf-8")
    (package / "permissions.yaml").write_text(yaml.safe_dump(dict(example.documents.permissions), sort_keys=False), encoding="utf-8")
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    for name, fixture in example.documents.dry_run_fixtures.items():
        (fixtures / name).write_text(yaml.safe_dump(dict(fixture), sort_keys=False), encoding="utf-8")
    if example.documents.rollback_text is not None:
        (package / "rollback.md").write_text(example.documents.rollback_text, encoding="utf-8")
    return package
