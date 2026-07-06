from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from appv24.workflows.activation import WorkflowActivationStore
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.scheduler import evaluate_trigger_tick
from appv24.workflows.webhook_ingress import WebhookReplayStore, handle_local_webhook


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
