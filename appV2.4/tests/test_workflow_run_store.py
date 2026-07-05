from __future__ import annotations

from pathlib import Path

from appv24.workflows.execution import WorkflowRunEvent
from appv24.workflows.run_store import WorkflowRunStore


def test_run_store_creates_run_directory_and_appends_events(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    run_id = store.create_run("telegram_support_ticket", "sha256:abc")
    event = WorkflowRunEvent(
        event_id="event-1",
        run_id=run_id,
        event_type="preview_created",
        package_fingerprint="sha256:abc",
        step_id=None,
        connector_action=None,
        status="passed",
        details={"fixture_name": "happy_path.yaml"},
    )

    store.append_event(event)

    assert (tmp_path / run_id / "run.json").is_file()
    assert store.read_events(run_id) == (event,)


def test_run_store_blocks_event_for_different_run_id(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    store.create_run("telegram_support_ticket", "sha256:abc")
    event = WorkflowRunEvent(
        event_id="event-1",
        run_id="missing",
        event_type="preview_created",
        package_fingerprint="sha256:abc",
        step_id=None,
        connector_action=None,
        status="passed",
        details={},
    )

    try:
        store.append_event(event)
    except ValueError as error:
        assert str(error) == "unknown workflow run: missing"
    else:
        raise AssertionError("expected unknown run to fail")


def test_run_store_rejects_secret_looking_event_details(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    run_id = store.create_run("telegram_support_ticket", "sha256:abc")
    event = WorkflowRunEvent(
        event_id="event-1",
        run_id=run_id,
        event_type="preview_created",
        package_fingerprint="sha256:abc",
        step_id=None,
        connector_action=None,
        status="blocked",
        details={"token": "sk-or-v1-1234567890abcdef1234567890abcdef"},
    )

    try:
        store.append_event(event)
    except ValueError as error:
        assert str(error) == "workflow run event contains a raw secret-looking value"
    else:
        raise AssertionError("expected raw secret event to fail")
