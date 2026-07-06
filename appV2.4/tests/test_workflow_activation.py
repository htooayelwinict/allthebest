from __future__ import annotations

import json
from pathlib import Path

import yaml

from appv24.workflows.execution import WorkflowRunMode
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows import WorkflowActivationStore, format_workflow_activation_list


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


def test_activation_store_creates_validated_local_activation(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")

    activation = store.create_activation(
        package,
        trigger_type="manual",
        fixture_name="happy_path.yaml",
        mode=WorkflowRunMode.LOCAL,
        approve_all=True,
    )

    assert activation.workflow_name == "telegram_support_ticket"
    assert activation.trigger_type == "manual"
    assert activation.fixture_name == "happy_path.yaml"
    assert activation.mode is WorkflowRunMode.LOCAL
    assert activation.enabled is True
    assert activation.last_run_id is None
    assert store.get_activation(activation.activation_id) == activation


def test_activation_run_executes_workflow_and_updates_status_history(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(
        package,
        trigger_type="manual",
        fixture_name="happy_path.yaml",
        mode=WorkflowRunMode.LOCAL,
        approve_all=True,
    )

    report = store.run_activation(activation.activation_id)

    updated = store.get_activation(activation.activation_id)
    history = store.history(activation.activation_id)
    status = store.status(activation.activation_id)
    assert report.status == "passed"
    assert report.terminal_step == "done"
    assert updated.last_run_id == report.run_id
    assert updated.last_status == "passed"
    assert history == (report.run_id,)
    assert status["activation_id"] == activation.activation_id
    assert status["last_run_id"] == report.run_id
    assert status["last_status"] == "passed"
    assert status["package_status"] == "current"
    run_dir = store.root / activation.activation_id / "runs" / report.run_id
    assert (run_dir / "report.json").is_file()
    run_manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_manifest["activation_id"] == activation.activation_id
    assert run_manifest["activation_trigger_type"] == "manual"
    assert run_manifest["mode"] == "local"
    assert run_manifest["fixture_name"] == "happy_path.yaml"


def test_disabled_activation_does_not_create_run(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(
        package,
        trigger_type="manual",
        fixture_name="happy_path.yaml",
        enabled=False,
        approve_all=True,
    )

    try:
        store.run_activation(activation.activation_id)
    except ValueError as error:
        assert str(error) == f"workflow activation is disabled: {activation.activation_id}"
    else:
        raise AssertionError("expected disabled activation to fail")

    assert store.history(activation.activation_id) == ()
    assert not (store.root / activation.activation_id / "runs").exists()


def test_activation_rejects_unknown_fixture_before_persisting(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")

    try:
        store.create_activation(package, fixture_name="missing.yaml", approve_all=True)
    except ValueError as error:
        assert str(error) == "unknown dry-run fixture for activation: missing.yaml"
    else:
        raise AssertionError("expected unknown fixture to fail")

    assert list(store.root.glob("*")) == []


def test_activation_history_rejects_unknown_activation(tmp_path: Path) -> None:
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")

    try:
        store.history("activation-missing")
    except ValueError as error:
        assert str(error) == "unknown workflow activation: activation-missing"
    else:
        raise AssertionError("expected unknown activation history to fail")


def test_activation_store_lists_activation_records(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")

    first = store.create_activation(package, fixture_name="happy_path.yaml", approve_all=True)
    second = store.create_activation(package, fixture_name="rejection_path.yaml", approve_all=True)

    listed = store.list_activations()

    assert {activation.activation_id for activation in listed} == {first.activation_id, second.activation_id}
    assert [activation.workflow_name for activation in listed] == [
        "telegram_support_ticket",
        "telegram_support_ticket",
    ]
    assert store.list_activations(root=tmp_path / "missing") == ()


def test_activation_store_can_disable_and_enable_activation(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(package, fixture_name="happy_path.yaml", approve_all=True)

    disabled = store.set_enabled(activation.activation_id, False)

    assert disabled.enabled is False
    assert store.get_activation(activation.activation_id).enabled is False
    try:
        store.run_activation(activation.activation_id)
    except ValueError as error:
        assert str(error) == f"workflow activation is disabled: {activation.activation_id}"
    else:
        raise AssertionError("expected disabled activation run to fail")

    enabled = store.set_enabled(activation.activation_id, True)

    assert enabled.enabled is True
    assert store.run_activation(activation.activation_id).status == "passed"


def test_activation_run_rejects_package_fingerprint_drift(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(package, fixture_name="happy_path.yaml", approve_all=True)
    workflow_path = package / "workflow.yaml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow_path.write_text(workflow_text.replace("telegram_support_ticket", "changed_ticket_workflow"), encoding="utf-8")

    try:
        store.run_activation(activation.activation_id)
    except ValueError as error:
        assert str(error).startswith(f"workflow activation package fingerprint changed: {activation.activation_id}")
    else:
        raise AssertionError("expected fingerprint drift to block activation run")

    assert store.history(activation.activation_id) == ()
    assert not (store.root / activation.activation_id / "runs").exists()


def test_activation_status_and_list_report_package_fingerprint_drift(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(package, fixture_name="happy_path.yaml", approve_all=True)
    workflow_path = package / "workflow.yaml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow_path.write_text(workflow_text.replace("telegram_support_ticket", "changed_ticket_workflow"), encoding="utf-8")

    status = store.status(activation.activation_id)
    list_statuses = store.list_statuses()
    rendered = format_workflow_activation_list(list_statuses)

    assert status["package_status"] == "changed"
    assert status["package_fingerprint"] == activation.package_fingerprint
    assert status["current_package_fingerprint"] != activation.package_fingerprint
    assert list_statuses[0]["activation_id"] == activation.activation_id
    assert list_statuses[0]["package_status"] == "changed"
    assert "package=changed" in rendered
