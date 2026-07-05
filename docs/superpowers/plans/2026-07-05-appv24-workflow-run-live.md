# appv24 Workflow Run-Live Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the appv24 workflow execution path from safe dry-run preview toward eventual approval-gated live execution, keeping live external side effects disabled until green-zone contracts are proven.

**Architecture:** Implement a headless workflow execution control plane under `appV2.4/appv24/workflows/`. The control plane treats package validation, preview, run persistence, approval, credential preflight, local artifact execution, IaC export, and live broker modes as explicit stages with hard gates. CLI/TUI wrappers stay deferred until the Python APIs have offline tests.

**Tech Stack:** Python dataclasses, pathlib, json/jsonl files, existing appv24 workflow loader/compiler/policy/simulator/broker modules, pytest.

---

## File Structure

Create:

- `appV2.4/appv24/workflows/execution.py`
  Defines `WorkflowRunMode`, `WorkflowRunRequest`, `WorkflowRunEvent`, and `WorkflowRunReport`.

- `appV2.4/appv24/workflows/fingerprints.py`
  Computes deterministic package fingerprints from `PackageDocuments`.

- `appV2.4/appv24/workflows/run_store.py`
  Persists append-only workflow run records under `.appv24/workflow-runs/<run_id>/`.

- `appV2.4/appv24/workflows/local_artifacts.py`
  Provides no-network broker providers that create local artifacts only inside a run artifact directory.

- `appV2.4/appv24/workflows/approval_gate.py`
  Creates approval requests, records approval decisions, and blocks stale package resume.

- `appV2.4/appv24/workflows/credential_refs.py`
  Validates credential references and requested scopes without loading raw secret values into package or ledger objects.

- `appV2.4/appv24/workflows/idempotency.py`
  Stores and checks idempotency keys for mutating broker actions.

- `appV2.4/appv24/workflows/iac_export.py`
  Exports deployable intent: environment requirements, secret refs, webhooks, schedules, connector scopes, approval policy, and rollback requirements.

- `appV2.4/appv24/workflows/live_broker.py`
  Defines live-read and live-draft broker contracts with injected fake transports for tests; live-production remains blocked by default.

Modify:

- `appV2.4/appv24/workflows/run_preview.py`
  Attach package fingerprints and optionally emit run events.

- `appV2.4/appv24/workflows/policy.py`
  Keep catalog enforcement for all brokered actions.

- `appV2.4/appv24/workflows/catalog.py`
  Expand action metadata as new fake transports and intent exports need it.

- `appV2.4/appv24/workflows/__init__.py`
  Export only stable public APIs after tests pass.

Test:

- `appV2.4/tests/test_workflow_run_preview.py`
- `appV2.4/tests/test_workflow_run_store.py`
- `appV2.4/tests/test_workflow_local_artifacts.py`
- `appV2.4/tests/test_workflow_approval_gate.py`
- `appV2.4/tests/test_workflow_credential_refs.py`
- `appV2.4/tests/test_workflow_idempotency.py`
- `appV2.4/tests/test_workflow_iac_export.py`
- `appV2.4/tests/test_workflow_live_broker.py`

Docs:

- `docs/appv24/workflow-run-preview-qa.md`
- `docs/appv24/workflow-run-live-path.md`

Do not modify red-zone files. Do not modify yellow-zone files until the final wrapper phase is explicitly selected.

---

### Task 1: Land Dry-Run Run Preview Contract

**Files:**

- Modify: `appV2.4/appv24/workflows/run_preview.py`
- Modify: `appV2.4/appv24/workflows/catalog.py`
- Modify: `appV2.4/appv24/workflows/examples.py`
- Modify: `appV2.4/appv24/workflows/policy.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`
- Test: `appV2.4/tests/test_workflow_run_preview.py`
- Test: `appV2.4/tests/test_workflow_examples.py`
- Test: `appV2.4/tests/test_workflow_ir.py`
- Docs: `docs/appv24/workflow-run-preview-qa.md`

- [ ] **Step 1: Verify current run-preview tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider \
  appV2.4/tests/test_workflow_run_preview.py \
  appV2.4/tests/test_workflow_examples.py \
  appV2.4/tests/test_workflow_ir.py::test_policy_blocks_unknown_catalog_action_even_when_read_only -q
```

Expected:

```text
11 passed
```

- [ ] **Step 2: Verify workflow suite**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow*.py -q
```

Expected:

```text
all workflow tests pass
```

- [ ] **Step 3: Commit the run-preview baseline**

Run:

```bash
git add \
  appV2.4/appv24/workflows/__init__.py \
  appV2.4/appv24/workflows/catalog.py \
  appV2.4/appv24/workflows/examples.py \
  appV2.4/appv24/workflows/policy.py \
  appV2.4/appv24/workflows/run_preview.py \
  appV2.4/tests/test_workflow_examples.py \
  appV2.4/tests/test_workflow_ir.py \
  appV2.4/tests/test_workflow_run_preview.py \
  docs/appv24/workflow-run-preview-qa.md
git commit -m "feat(appv24): add workflow run preview"
```

Expected:

```text
commit succeeds and prints the subject: feat(appv24): add workflow run preview
```

---

### Task 2: Add Execution Data Contracts

**Files:**

- Create: `appV2.4/appv24/workflows/execution.py`
- Test: `appV2.4/tests/test_workflow_execution.py`

- [ ] **Step 1: Write failing execution contract tests**

Create `appV2.4/tests/test_workflow_execution.py`:

```python
from __future__ import annotations

from pathlib import Path

from appv24.workflows.execution import (
    WorkflowRunEvent,
    WorkflowRunMode,
    WorkflowRunReport,
    WorkflowRunRequest,
)


def test_workflow_run_request_defaults_to_preview_without_allowlist() -> None:
    request = WorkflowRunRequest(package_path=Path("pkg"), fixture_name="happy_path.yaml")

    assert request.mode is WorkflowRunMode.PREVIEW
    assert request.connector_allowlist == ()
    assert request.run_root is None


def test_workflow_run_event_round_trips_through_json_dict() -> None:
    event = WorkflowRunEvent(
        event_id="event-1",
        run_id="run-1",
        event_type="broker_action_planned",
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        status="planned",
        details={"allowed_outputs": ["message_id"]},
    )

    assert WorkflowRunEvent.from_dict(event.to_dict()) == event


def test_workflow_run_report_serializes_core_fields() -> None:
    report = WorkflowRunReport(
        run_id="run-1",
        workflow_name="telegram_support_ticket",
        mode=WorkflowRunMode.PREVIEW,
        status="passed",
        package_fingerprint="sha256:abc",
        terminal_step="done",
        step_trace=(),
        brokered_actions=(),
        approvals=(),
        events=(),
        issues=(),
    )

    assert report.to_dict()["mode"] == "preview"
    assert report.to_dict()["package_fingerprint"] == "sha256:abc"
```

- [ ] **Step 2: Run execution contract tests and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_execution.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.execution'
```

- [ ] **Step 3: Implement execution contracts**

Create `appV2.4/appv24/workflows/execution.py`:

```python
"""Workflow run execution data contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.ledger import StepTrace
from appv24.workflows.run_preview import WorkflowApprovalPreview, WorkflowBrokeredActionPreview


class WorkflowRunMode(StrEnum):
    PREVIEW = "preview"
    LOCAL = "local"
    STAGED = "staged"
    CREDENTIAL_PREFLIGHT = "credential-preflight"
    LIVE_READ = "live-read"
    LIVE_DRAFT = "live-draft"
    LIVE_PRODUCTION = "live-production"


@dataclass(frozen=True)
class WorkflowRunRequest:
    package_path: Path
    fixture_name: str | None = None
    mode: WorkflowRunMode = WorkflowRunMode.PREVIEW
    run_root: Path | None = None
    approval_run_id: str | None = None
    connector_allowlist: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowRunEvent:
    event_id: str
    run_id: str
    event_type: str
    package_fingerprint: str
    step_id: str | None
    connector_action: str | None
    status: str
    details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowRunEvent":
        return cls(
            event_id=str(value["event_id"]),
            run_id=str(value["run_id"]),
            event_type=str(value["event_type"]),
            package_fingerprint=str(value["package_fingerprint"]),
            step_id=value["step_id"] if isinstance(value.get("step_id"), str) else None,
            connector_action=value["connector_action"] if isinstance(value.get("connector_action"), str) else None,
            status=str(value["status"]),
            details=value["details"] if isinstance(value.get("details"), dict) else {},
        )


@dataclass(frozen=True)
class WorkflowRunReport:
    run_id: str
    workflow_name: str
    mode: WorkflowRunMode
    status: str
    package_fingerprint: str
    terminal_step: str | None
    step_trace: tuple[StepTrace, ...]
    brokered_actions: tuple[WorkflowBrokeredActionPreview, ...]
    approvals: tuple[WorkflowApprovalPreview, ...]
    events: tuple[WorkflowRunEvent, ...]
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["mode"] = self.mode.value
        return data
```

- [ ] **Step 4: Run execution contract tests and verify pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_execution.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Export execution contracts**

Modify `appV2.4/appv24/workflows/__init__.py`:

```python
from appv24.workflows.execution import (
    WorkflowRunEvent,
    WorkflowRunMode,
    WorkflowRunReport,
    WorkflowRunRequest,
)
```

Add the four names to `__all__`.

- [ ] **Step 6: Run workflow execution tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_execution.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 7: Commit execution contracts**

Run:

```bash
git add appV2.4/appv24/workflows/execution.py appV2.4/appv24/workflows/__init__.py appV2.4/tests/test_workflow_execution.py
git commit -m "feat(appv24): add workflow run execution contracts"
```

---

### Task 3: Add Deterministic Package Fingerprints

**Files:**

- Create: `appV2.4/appv24/workflows/fingerprints.py`
- Modify: `appV2.4/appv24/workflows/run_preview.py`
- Test: `appV2.4/tests/test_workflow_fingerprints.py`

- [ ] **Step 1: Write failing fingerprint tests**

Create `appV2.4/tests/test_workflow_fingerprints.py`:

```python
from __future__ import annotations

from copy import deepcopy

from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.fingerprints import fingerprint_package_documents
from appv24.workflows.loader import PackageDocuments


def test_package_fingerprint_is_stable_for_same_documents() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    assert fingerprint_package_documents(example.documents) == fingerprint_package_documents(example.documents)


def test_package_fingerprint_changes_when_workflow_changes() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    workflow = deepcopy(example.documents.workflow)
    workflow["description"] = "changed"
    changed = PackageDocuments(
        root=example.documents.root,
        workflow=workflow,
        connectors=example.documents.connectors,
        permissions=example.documents.permissions,
        dry_run_fixtures=example.documents.dry_run_fixtures,
        rollback_text=example.documents.rollback_text,
    )

    assert fingerprint_package_documents(example.documents) != fingerprint_package_documents(changed)


def test_package_fingerprint_uses_sha256_prefix() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    assert fingerprint_package_documents(example.documents).startswith("sha256:")
```

- [ ] **Step 2: Run fingerprint tests and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_fingerprints.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.fingerprints'
```

- [ ] **Step 3: Implement deterministic fingerprinting**

Create `appV2.4/appv24/workflows/fingerprints.py`:

```python
"""Deterministic workflow package fingerprints."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from appv24.workflows.loader import PackageDocuments


def fingerprint_package_documents(documents: PackageDocuments) -> str:
    payload = {
        "workflow": _normalize(documents.workflow),
        "connectors": _normalize(documents.connectors),
        "permissions": _normalize(documents.permissions),
        "dry_run_fixtures": _normalize(documents.dry_run_fixtures),
        "rollback_text": documents.rollback_text or "",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize(item) for item in value]
    return value
```

- [ ] **Step 4: Run fingerprint tests and verify pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_fingerprints.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Add package fingerprint to run preview**

Modify `appV2.4/appv24/workflows/run_preview.py`:

```python
from appv24.workflows.fingerprints import fingerprint_package_documents
```

Add this field to `WorkflowRunPreview`:

```python
package_fingerprint: str
```

When constructing any preview, pass:

```python
package_fingerprint=fingerprint_package_documents(documents)
```

When constructing a blocked preview before documents are unavailable, use the fingerprint of the loaded documents passed into `build_run_preview`.

- [ ] **Step 6: Add run-preview fingerprint test**

Append to `appV2.4/tests/test_workflow_run_preview.py`:

```python
def test_run_preview_includes_package_fingerprint() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    preview = build_run_preview(example.documents, "happy_path.yaml", broker=example.broker)

    assert preview.package_fingerprint.startswith("sha256:")
```

- [ ] **Step 7: Run preview and fingerprint tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider \
  appV2.4/tests/test_workflow_fingerprints.py \
  appV2.4/tests/test_workflow_run_preview.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 8: Commit fingerprint work**

Run:

```bash
git add appV2.4/appv24/workflows/fingerprints.py appV2.4/appv24/workflows/run_preview.py appV2.4/tests/test_workflow_fingerprints.py appV2.4/tests/test_workflow_run_preview.py
git commit -m "feat(appv24): fingerprint workflow run previews"
```

---

### Task 4: Add Append-Only Run Store

**Files:**

- Create: `appV2.4/appv24/workflows/run_store.py`
- Test: `appV2.4/tests/test_workflow_run_store.py`

- [ ] **Step 1: Write failing run-store tests**

Create `appV2.4/tests/test_workflow_run_store.py`:

```python
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
```

- [ ] **Step 2: Run run-store tests and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_run_store.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.run_store'
```

- [ ] **Step 3: Implement run store**

Create `appV2.4/appv24/workflows/run_store.py`:

```python
"""Append-only workflow run storage."""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

from appv24.workflows.execution import WorkflowRunEvent

SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|sk-or-v1-[A-Za-z0-9_-]+|ghp_[A-Za-z0-9_]+)")


class WorkflowRunStore:
    def __init__(self, root: Path):
        self.root = root

    def create_run(self, workflow_name: str, package_fingerprint: str) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        run_id = f"run-{uuid4().hex}"
        run_dir = self.root / run_id
        run_dir.mkdir()
        (run_dir / "run.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "workflow_name": workflow_name,
                    "package_fingerprint": package_fingerprint,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (run_dir / "events.jsonl").write_text("", encoding="utf-8")
        return run_id

    def append_event(self, event: WorkflowRunEvent) -> None:
        run_dir = self.root / event.run_id
        if not run_dir.is_dir():
            raise ValueError(f"unknown workflow run: {event.run_id}")
        encoded = json.dumps(event.to_dict(), sort_keys=True)
        if SECRET_VALUE_RE.search(encoded):
            raise ValueError("workflow run event contains a raw secret-looking value")
        with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")

    def read_events(self, run_id: str) -> tuple[WorkflowRunEvent, ...]:
        path = self.root / run_id / "events.jsonl"
        if not path.is_file():
            raise ValueError(f"unknown workflow run: {run_id}")
        events: list[WorkflowRunEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(WorkflowRunEvent.from_dict(json.loads(line)))
        return tuple(events)
```

- [ ] **Step 4: Run run-store tests and verify pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_run_store.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit run store**

Run:

```bash
git add appV2.4/appv24/workflows/run_store.py appV2.4/tests/test_workflow_run_store.py
git commit -m "feat(appv24): add workflow run store"
```

---

### Task 5: Persist Run Preview Reports

**Files:**

- Modify: `appV2.4/appv24/workflows/run_preview.py`
- Modify: `appV2.4/appv24/workflows/run_store.py`
- Test: `appV2.4/tests/test_workflow_run_preview.py`

- [ ] **Step 1: Write failing persisted-preview test**

Append to `appV2.4/tests/test_workflow_run_preview.py`:

```python
from appv24.workflows.run_store import WorkflowRunStore
from appv24.workflows.run_preview import persist_run_preview


def test_persist_run_preview_writes_preview_and_events(tmp_path: Path) -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    preview = build_run_preview(example.documents, "happy_path.yaml", broker=example.broker)
    store = WorkflowRunStore(tmp_path)

    run_id = persist_run_preview(store, preview)

    assert (tmp_path / run_id / "preview.json").is_file()
    events = store.read_events(run_id)
    assert events[0].event_type == "preview_created"
    assert events[0].package_fingerprint == preview.package_fingerprint
```

- [ ] **Step 2: Run persisted-preview test and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_run_preview.py::test_persist_run_preview_writes_preview_and_events -q
```

Expected:

```text
ImportError: cannot import name 'persist_run_preview'
```

- [ ] **Step 3: Implement persisted preview**

Modify `appV2.4/appv24/workflows/run_preview.py`:

```python
import json

from appv24.workflows.execution import WorkflowRunEvent
from appv24.workflows.run_store import WorkflowRunStore
```

Add:

```python
def persist_run_preview(store: WorkflowRunStore, preview: WorkflowRunPreview) -> str:
    run_id = store.create_run(preview.workflow_name, preview.package_fingerprint)
    preview_path = store.root / run_id / "preview.json"
    preview_path.write_text(json.dumps(preview.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    store.append_event(
        WorkflowRunEvent(
            event_id="event-1",
            run_id=run_id,
            event_type="preview_created",
            package_fingerprint=preview.package_fingerprint,
            step_id=None,
            connector_action=None,
            status=preview.status,
            details={"fixture_name": preview.fixture_name},
        )
    )
    for index, event in enumerate(preview.ledger_preview, start=2):
        store.append_event(
            WorkflowRunEvent(
                event_id=f"event-{index}",
                run_id=run_id,
                event_type=event.event_type,
                package_fingerprint=preview.package_fingerprint,
                step_id=event.step_id,
                connector_action=event.connector_action,
                status=event.status,
                details={
                    "approval_id": event.approval_id,
                    "broker_provider_id": event.broker_provider_id,
                    "outputs": dict(event.outputs or {}),
                },
            )
        )
    return run_id
```

- [ ] **Step 4: Run preview and run-store tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider \
  appV2.4/tests/test_workflow_run_preview.py \
  appV2.4/tests/test_workflow_run_store.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit persisted preview**

Run:

```bash
git add appV2.4/appv24/workflows/run_preview.py appV2.4/tests/test_workflow_run_preview.py
git commit -m "feat(appv24): persist workflow run previews"
```

---

### Task 6: Add Local Artifact Broker Providers

**Files:**

- Create: `appV2.4/appv24/workflows/local_artifacts.py`
- Test: `appV2.4/tests/test_workflow_local_artifacts.py`

- [ ] **Step 1: Write failing local artifact tests**

Create `appV2.4/tests/test_workflow_local_artifacts.py`:

```python
from __future__ import annotations

from pathlib import Path

from appv24.workflows.broker import BrokerRequest
from appv24.workflows.local_artifacts import LocalArtifactProviders


def test_ticket_draft_provider_writes_only_under_artifact_root(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)
    result = providers.ticket_create_draft(
        BrokerRequest(
            step_id="create_ticket_draft",
            connector_action="ticket.create_draft",
            input_ref={"with": {"title": "Support request"}},
            fixture={},
            allowed_outputs=("ticket_id", "artifact_path"),
        )
    )

    assert result.status == "simulated"
    assert result.outputs["ticket_id"].startswith("local-ticket-")
    artifact_path = Path(str(result.outputs["artifact_path"]))
    assert artifact_path.is_file()
    assert artifact_path.is_relative_to(tmp_path)


def test_local_artifact_provider_filters_unallowed_outputs(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)
    result = providers.gmail_create_draft(
        BrokerRequest(
            step_id="create_gmail_draft",
            connector_action="gmail.create_draft",
            input_ref={},
            fixture={},
            allowed_outputs=("draft_id",),
        )
    )

    assert result.outputs == {"draft_id": "local-draft-create_gmail_draft"}
```

- [ ] **Step 2: Run local artifact tests and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_local_artifacts.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.local_artifacts'
```

- [ ] **Step 3: Implement local artifact providers**

Create `appV2.4/appv24/workflows/local_artifacts.py`:

```python
"""Local no-network artifact providers for workflow broker dry-runs."""

from __future__ import annotations

import json
from pathlib import Path

from appv24.workflows.broker import BrokerRequest, BrokerResult


class LocalArtifactProviders:
    def __init__(self, artifact_root: Path):
        self.artifact_root = artifact_root
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def ticket_create_draft(self, request: BrokerRequest) -> BrokerResult:
        ticket_id = f"local-ticket-{request.step_id}"
        path = self.artifact_root / f"{ticket_id}.json"
        path.write_text(json.dumps({"step_id": request.step_id, "input": request.input_ref}, sort_keys=True) + "\n", encoding="utf-8")
        return _filtered(
            request,
            {
                "ticket_id": ticket_id,
                "artifact_path": str(path),
            },
        )

    def gmail_create_draft(self, request: BrokerRequest) -> BrokerResult:
        draft_id = f"local-draft-{request.step_id}"
        path = self.artifact_root / f"{draft_id}.json"
        path.write_text(json.dumps({"step_id": request.step_id, "input": request.input_ref}, sort_keys=True) + "\n", encoding="utf-8")
        return _filtered(
            request,
            {
                "draft_id": draft_id,
                "artifact_path": str(path),
            },
        )


def _filtered(request: BrokerRequest, outputs: dict[str, object]) -> BrokerResult:
    allowed = set(request.allowed_outputs)
    return BrokerResult(
        status="simulated",
        provider_id="local_artifacts",
        outputs={key: value for key, value in outputs.items() if key in allowed},
    )
```

- [ ] **Step 4: Run local artifact tests and verify pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_local_artifacts.py -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit local artifact providers**

Run:

```bash
git add appV2.4/appv24/workflows/local_artifacts.py appV2.4/tests/test_workflow_local_artifacts.py
git commit -m "feat(appv24): add local workflow artifact providers"
```

---

### Task 7: Add Approval Gate And Stale Package Blocking

**Files:**

- Create: `appV2.4/appv24/workflows/approval_gate.py`
- Test: `appV2.4/tests/test_workflow_approval_gate.py`

- [ ] **Step 1: Write failing approval gate tests**

Create `appV2.4/tests/test_workflow_approval_gate.py`:

```python
from __future__ import annotations

from appv24.workflows.approval_gate import (
    ApprovalDecision,
    ApprovalRequest,
    create_approval_request,
    resume_approval,
)


def test_create_approval_request_records_package_fingerprint_and_action() -> None:
    request = create_approval_request(
        run_id="run-1",
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        approval_id="admin_approval",
        allowed_outputs=("message_id",),
        idempotency_key_fields=("ticket_id", "admin_chat_id"),
    )

    assert request.status == "pending"
    assert request.package_fingerprint == "sha256:abc"
    assert request.connector_action == "telegram.send_message"


def test_resume_approval_blocks_stale_package_fingerprint() -> None:
    request = ApprovalRequest(
        approval_request_id="approval-1",
        run_id="run-1",
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        approval_id="admin_approval",
        allowed_outputs=("message_id",),
        idempotency_key_fields=("ticket_id",),
        status="pending",
    )
    decision = ApprovalDecision(approved=True, package_fingerprint="sha256:changed", decided_by="Lewis")

    try:
        resume_approval(request, decision)
    except ValueError as error:
        assert str(error) == "approval package fingerprint is stale"
    else:
        raise AssertionError("expected stale approval to fail")


def test_resume_approval_returns_approved_status() -> None:
    request = create_approval_request(
        run_id="run-1",
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        approval_id="admin_approval",
        allowed_outputs=("message_id",),
        idempotency_key_fields=("ticket_id",),
    )
    decision = ApprovalDecision(approved=True, package_fingerprint="sha256:abc", decided_by="Lewis")

    assert resume_approval(request, decision).status == "approved"
```

- [ ] **Step 2: Run approval gate tests and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_approval_gate.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.approval_gate'
```

- [ ] **Step 3: Implement approval gate**

Create `appV2.4/appv24/workflows/approval_gate.py`:

```python
"""Human approval gate contracts for workflow runs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import uuid4


@dataclass(frozen=True)
class ApprovalRequest:
    approval_request_id: str
    run_id: str
    package_fingerprint: str
    step_id: str
    connector_action: str
    approval_id: str
    allowed_outputs: tuple[str, ...]
    idempotency_key_fields: tuple[str, ...]
    status: str = "pending"


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    package_fingerprint: str
    decided_by: str


def create_approval_request(
    *,
    run_id: str,
    package_fingerprint: str,
    step_id: str,
    connector_action: str,
    approval_id: str,
    allowed_outputs: tuple[str, ...],
    idempotency_key_fields: tuple[str, ...],
) -> ApprovalRequest:
    return ApprovalRequest(
        approval_request_id=f"approval-{uuid4().hex}",
        run_id=run_id,
        package_fingerprint=package_fingerprint,
        step_id=step_id,
        connector_action=connector_action,
        approval_id=approval_id,
        allowed_outputs=allowed_outputs,
        idempotency_key_fields=idempotency_key_fields,
    )


def resume_approval(request: ApprovalRequest, decision: ApprovalDecision) -> ApprovalRequest:
    if request.package_fingerprint != decision.package_fingerprint:
        raise ValueError("approval package fingerprint is stale")
    return replace(request, status="approved" if decision.approved else "denied")
```

- [ ] **Step 4: Run approval gate tests and verify pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_approval_gate.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit approval gate**

Run:

```bash
git add appV2.4/appv24/workflows/approval_gate.py appV2.4/tests/test_workflow_approval_gate.py
git commit -m "feat(appv24): add workflow approval gate"
```

---

### Task 8: Add Credential Reference Preflight

**Files:**

- Create: `appV2.4/appv24/workflows/credential_refs.py`
- Test: `appV2.4/tests/test_workflow_credential_refs.py`

- [ ] **Step 1: Write failing credential tests**

Create `appV2.4/tests/test_workflow_credential_refs.py`:

```python
from __future__ import annotations

from appv24.workflows.credential_refs import (
    CredentialRefResolver,
    CredentialScope,
    preflight_credential_refs,
)
from appv24.workflows.examples import golden_workflow_examples


def test_credential_preflight_accepts_available_scopes() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    resolver = CredentialRefResolver(
        {
            "ticket": CredentialScope(available=True, scopes=("ticket:draft",)),
            "telegram": CredentialScope(available=True, scopes=("telegram:send",)),
        }
    )

    report = preflight_credential_refs(example.documents, resolver)

    assert report.ok is True
    assert report.issues == ()


def test_credential_preflight_blocks_missing_connector_ref() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    resolver = CredentialRefResolver({"ticket": CredentialScope(available=True, scopes=("ticket:draft",))})

    report = preflight_credential_refs(example.documents, resolver)

    assert report.ok is False
    assert "missing credential ref for telegram" in report.issues


def test_credential_preflight_blocks_wildcard_scope() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    resolver = CredentialRefResolver(
        {
            "ticket": CredentialScope(available=True, scopes=("*",)),
            "telegram": CredentialScope(available=True, scopes=("telegram:send",)),
        }
    )

    report = preflight_credential_refs(example.documents, resolver)

    assert report.ok is False
    assert "wildcard credential scope is not allowed for ticket" in report.issues
```

- [ ] **Step 2: Run credential tests and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_credential_refs.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.credential_refs'
```

- [ ] **Step 3: Implement credential preflight**

Create `appV2.4/appv24/workflows/credential_refs.py`:

```python
"""Credential reference preflight for workflow packages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from appv24.workflows.loader import PackageDocuments


@dataclass(frozen=True)
class CredentialScope:
    available: bool
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class CredentialPreflightReport:
    issues: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


class CredentialRefResolver:
    def __init__(self, credentials: Mapping[str, CredentialScope]):
        self._credentials = dict(credentials)

    def resolve(self, connector_id: str) -> CredentialScope | None:
        return self._credentials.get(connector_id)


def preflight_credential_refs(
    documents: PackageDocuments,
    resolver: CredentialRefResolver,
) -> CredentialPreflightReport:
    issues: list[str] = []
    for permission in documents.permissions.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        connector_id = str(permission["connector"])
        credential = resolver.resolve(connector_id)
        if credential is None or not credential.available:
            issues.append(f"missing credential ref for {connector_id}")
            continue
        if "*" in credential.scopes:
            issues.append(f"wildcard credential scope is not allowed for {connector_id}")
        requested = tuple(str(scope) for scope in permission.get("scopes", []) if isinstance(scope, str))
        for scope in requested:
            if scope not in credential.scopes:
                issues.append(f"credential for {connector_id} does not grant {scope}")
    return CredentialPreflightReport(issues=tuple(issues))
```

- [ ] **Step 4: Run credential tests and verify pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_credential_refs.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit credential preflight**

Run:

```bash
git add appV2.4/appv24/workflows/credential_refs.py appV2.4/tests/test_workflow_credential_refs.py
git commit -m "feat(appv24): add workflow credential preflight"
```

---

### Task 9: Add Idempotency Store

**Files:**

- Create: `appV2.4/appv24/workflows/idempotency.py`
- Test: `appV2.4/tests/test_workflow_idempotency.py`

- [ ] **Step 1: Write failing idempotency tests**

Create `appV2.4/tests/test_workflow_idempotency.py`:

```python
from __future__ import annotations

from appv24.workflows.idempotency import IdempotencyStore


def test_idempotency_store_returns_existing_result_for_same_payload() -> None:
    store = IdempotencyStore()

    first = store.record_or_reuse("key-1", {"ticket_id": "ticket-1"}, payload_hash="payload-a")
    second = store.record_or_reuse("key-1", {"ticket_id": "ticket-2"}, payload_hash="payload-a")

    assert first == {"ticket_id": "ticket-1"}
    assert second == {"ticket_id": "ticket-1"}


def test_idempotency_store_blocks_conflicting_payload() -> None:
    store = IdempotencyStore()
    store.record_or_reuse("key-1", {"ticket_id": "ticket-1"}, payload_hash="payload-a")

    try:
        store.record_or_reuse("key-1", {"ticket_id": "ticket-2"}, payload_hash="payload-b")
    except ValueError as error:
        assert str(error) == "idempotency key conflict for key-1"
    else:
        raise AssertionError("expected idempotency conflict")
```

- [ ] **Step 2: Run idempotency tests and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_idempotency.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.idempotency'
```

- [ ] **Step 3: Implement idempotency store**

Create `appV2.4/appv24/workflows/idempotency.py`:

```python
"""In-memory workflow idempotency store for broker action tests."""

from __future__ import annotations

from typing import Mapping


class IdempotencyStore:
    def __init__(self):
        self._entries: dict[str, tuple[str, Mapping[str, object]]] = {}

    def record_or_reuse(
        self,
        key: str,
        result: Mapping[str, object],
        *,
        payload_hash: str,
    ) -> Mapping[str, object]:
        existing = self._entries.get(key)
        if existing is not None:
            existing_hash, existing_result = existing
            if existing_hash != payload_hash:
                raise ValueError(f"idempotency key conflict for {key}")
            return existing_result
        self._entries[key] = (payload_hash, dict(result))
        return result
```

- [ ] **Step 4: Run idempotency tests and verify pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_idempotency.py -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit idempotency store**

Run:

```bash
git add appV2.4/appv24/workflows/idempotency.py appV2.4/tests/test_workflow_idempotency.py
git commit -m "feat(appv24): add workflow idempotency store"
```

---

### Task 10: Add IaC And Deployment Intent Export

**Files:**

- Create: `appV2.4/appv24/workflows/iac_export.py`
- Test: `appV2.4/tests/test_workflow_iac_export.py`

- [ ] **Step 1: Write failing IaC export tests**

Create `appV2.4/tests/test_workflow_iac_export.py`:

```python
from __future__ import annotations

from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.iac_export import export_deployment_intent


def test_deployment_intent_reports_scopes_approvals_and_rollback() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    compiled = compile_workflow_documents(example.documents)
    assert compiled.ir is not None

    intent = export_deployment_intent(compiled.ir, example.documents)

    assert intent.workflow_name == "telegram_support_ticket"
    assert intent.connector_scopes == {
        "ticket": ("ticket:draft",),
        "telegram": ("telegram:send",),
    }
    assert intent.approval_ids == ("admin_approval",)
    assert intent.rollback_required is True
    assert intent.webhook_triggers == ("telegram_message",)
```

- [ ] **Step 2: Run IaC export tests and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_iac_export.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.iac_export'
```

- [ ] **Step 3: Implement deployment intent export**

Create `appV2.4/appv24/workflows/iac_export.py`:

```python
"""Deployment intent export for appv24 workflow packages."""

from __future__ import annotations

from dataclasses import dataclass

from appv24.workflows.ir import WorkflowIR
from appv24.workflows.loader import PackageDocuments


@dataclass(frozen=True)
class WorkflowDeploymentIntent:
    workflow_name: str
    env_requirements: tuple[str, ...]
    secret_refs: tuple[str, ...]
    webhook_triggers: tuple[str, ...]
    scheduler_triggers: tuple[str, ...]
    connector_scopes: dict[str, tuple[str, ...]]
    approval_ids: tuple[str, ...]
    rollback_required: bool


def export_deployment_intent(ir: WorkflowIR, documents: PackageDocuments) -> WorkflowDeploymentIntent:
    connector_scopes: dict[str, tuple[str, ...]] = {}
    for permission in documents.permissions.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        connector_scopes[str(permission["connector"])] = tuple(
            str(scope) for scope in permission.get("scopes", []) if isinstance(scope, str)
        )
    return WorkflowDeploymentIntent(
        workflow_name=ir.name,
        env_requirements=tuple(f"APPV24_{connector.upper()}_CREDENTIAL_REF" for connector in sorted(connector_scopes)),
        secret_refs=tuple(f"{connector}.credential" for connector in sorted(connector_scopes)),
        webhook_triggers=tuple(trigger.trigger_id for trigger in ir.triggers if trigger.trigger_type == "webhook"),
        scheduler_triggers=tuple(trigger.trigger_id for trigger in ir.triggers if trigger.trigger_type == "schedule"),
        connector_scopes=connector_scopes,
        approval_ids=tuple(sorted(ir.approvals)),
        rollback_required=bool(documents.rollback_text and documents.rollback_text.strip()),
    )
```

- [ ] **Step 4: Run IaC export tests and verify pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_iac_export.py -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Commit deployment intent export**

Run:

```bash
git add appV2.4/appv24/workflows/iac_export.py appV2.4/tests/test_workflow_iac_export.py
git commit -m "feat(appv24): export workflow deployment intent"
```

---

### Task 11: Add Live Broker Contracts Disabled By Default

**Files:**

- Create: `appV2.4/appv24/workflows/live_broker.py`
- Test: `appV2.4/tests/test_workflow_live_broker.py`

- [ ] **Step 1: Write failing live broker tests**

Create `appV2.4/tests/test_workflow_live_broker.py`:

```python
from __future__ import annotations

from appv24.workflows.broker import BrokerRequest, BrokerResult
from appv24.workflows.live_broker import LiveWorkflowBroker, LiveWorkflowTransport


class FakeTransport(LiveWorkflowTransport):
    def invoke(self, request: BrokerRequest) -> BrokerResult:
        return BrokerResult(status="live-read", provider_id="fake", outputs={"alerts": [], "secret": "blocked"})


def test_live_broker_blocks_without_action_allowlist() -> None:
    broker = LiveWorkflowBroker({}, mode="live-read")
    result = broker.invoke(
        BrokerRequest(
            step_id="inspect_alerts",
            connector_action="github.list_dependabot_alerts",
            input_ref={},
            fixture={},
            allowed_outputs=("alerts",),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.issues == ("live action is not allowlisted: github.list_dependabot_alerts",)


def test_live_broker_allows_read_only_fake_transport_when_allowlisted() -> None:
    broker = LiveWorkflowBroker({"github.list_dependabot_alerts": FakeTransport()}, mode="live-read")
    result = broker.invoke(
        BrokerRequest(
            step_id="inspect_alerts",
            connector_action="github.list_dependabot_alerts",
            input_ref={},
            fixture={},
            allowed_outputs=("alerts",),
            dry_run=False,
        )
    )

    assert result.status == "live-read"
    assert result.outputs == {"alerts": []}


def test_live_broker_blocks_production_mode_by_default() -> None:
    broker = LiveWorkflowBroker({"github.list_dependabot_alerts": FakeTransport()}, mode="live-production")
    result = broker.invoke(
        BrokerRequest(
            step_id="inspect_alerts",
            connector_action="github.list_dependabot_alerts",
            input_ref={},
            fixture={},
            allowed_outputs=("alerts",),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.issues == ("live-production broker is disabled by default",)
```

- [ ] **Step 2: Run live broker tests and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_live_broker.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.live_broker'
```

- [ ] **Step 3: Implement disabled-by-default live broker**

Create `appV2.4/appv24/workflows/live_broker.py`:

```python
"""Live broker contracts for workflow runs.

Tests use injected fake transports. Real provider transports remain manual-gated.
"""

from __future__ import annotations

from typing import Protocol

from appv24.workflows.broker import BrokerRequest, BrokerResult


class LiveWorkflowTransport(Protocol):
    def invoke(self, request: BrokerRequest) -> BrokerResult:
        raise NotImplementedError


class LiveWorkflowBroker:
    def __init__(self, transports: dict[str, LiveWorkflowTransport], *, mode: str):
        self._transports = dict(transports)
        self._mode = mode

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        if self._mode == "live-production":
            return BrokerResult(
                status="blocked",
                provider_id="live",
                outputs={},
                issues=("live-production broker is disabled by default",),
            )
        transport = self._transports.get(request.connector_action)
        if transport is None:
            return BrokerResult(
                status="blocked",
                provider_id="live",
                outputs={},
                issues=(f"live action is not allowlisted: {request.connector_action}",),
            )
        result = transport.invoke(request)
        allowed = set(request.allowed_outputs)
        return BrokerResult(
            status=result.status,
            provider_id=result.provider_id,
            outputs={key: value for key, value in result.outputs.items() if key in allowed},
            issues=result.issues,
        )
```

- [ ] **Step 4: Run live broker tests and verify pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_live_broker.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit live broker contract**

Run:

```bash
git add appV2.4/appv24/workflows/live_broker.py appV2.4/tests/test_workflow_live_broker.py
git commit -m "feat(appv24): add disabled live workflow broker"
```

---

### Task 12: Add Run-Live Path Documentation

**Files:**

- Create: `docs/appv24/workflow-run-live-path.md`
- Modify: `docs/appv24/workflow-enable-v0.md`

- [ ] **Step 1: Create operator-facing run-live path document**

Create `docs/appv24/workflow-run-live-path.md`:

```markdown
# appv24 Workflow Run-Live Path

appv24 workflow execution moves through staged modes:

1. `preview`
2. `local`
3. `staged`
4. `credential-preflight`
5. `live-read`
6. `live-draft`
7. `live-production`

Live production is disabled by default.

Before any live mutation, appv24 requires:

- package validation
- compile success
- policy success
- exact capability catalog match
- package fingerprint lock
- credential reference preflight
- explicit approval for current package fingerprint
- idempotency key material
- rollback coverage
- connector action allowlist
- prior dry-run or local run for the same package fingerprint

Normal CI uses fake transports only. Real provider checks are manual-gated.
```

- [ ] **Step 2: Link from enablement v0 docs**

Append to `docs/appv24/workflow-enable-v0.md`:

```markdown
## Next Path

The staged execution roadmap is documented in `docs/appv24/workflow-run-live-path.md`.
```

- [ ] **Step 3: Run docs grep check**

Run:

```bash
rg -l "Live production is disabled by default|workflow-run-live-path" docs/appv24
```

Expected:

```text
docs/appv24/workflow-enable-v0.md
docs/appv24/workflow-run-live-path.md
```

- [ ] **Step 4: Commit docs**

Run:

```bash
git add docs/appv24/workflow-run-live-path.md docs/appv24/workflow-enable-v0.md
git commit -m "docs(appv24): document workflow run-live path"
```

---

### Task 13: Final Verification And Zone Audit

**Files:**

- Inspect all changed files.

- [ ] **Step 1: Run workflow tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow*.py -q
```

Expected:

```text
all workflow tests pass
```

- [ ] **Step 2: Run full appV2.4 tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests -q
```

Expected:

```text
all appV2.4 tests pass
```

- [ ] **Step 3: Check formatting whitespace**

Run:

```bash
git diff --check
```

Expected:

```text
no output
```

- [ ] **Step 4: Audit red/yellow-zone changes**

Run:

```bash
changed=$(git diff --name-only HEAD)
printf '%s\n' "$changed" | rg 'appV2\.4/appv24/(agent/|app\.py|ai/stream\.py|ai/validation\.py|compaction/|coding_agent/session_store\.py|cli\.py|tui/|profiles/|ai/providers/)'
```

Expected:

```text
no output
```

- [ ] **Step 5: Inspect final diff**

Run:

```bash
git diff --stat HEAD
```

Expected:

```text
only workflow modules, workflow tests, and docs are listed
```

- [ ] **Step 6: Commit final verification notes if docs changed**

If Task 12 docs were adjusted during verification, commit them:

```bash
git add docs/appv24/workflow-run-live-path.md docs/appv24/workflow-enable-v0.md
git commit -m "docs(appv24): update workflow run-live verification notes"
```

If no docs changed, do not create an empty commit.

## Execution Notes

This plan deliberately does not add real Gmail, Telegram, GitHub, MCP, cloud, shell, or webhook execution. Real provider checks must be manual-gated and excluded from normal CI.

The earliest live-capable milestone is `live-read` with fake transport tests. The earliest mutating milestone is `live-draft`, and it must remain allowlist-only with explicit approval, credential preflight, idempotency, and rollback gates.

Live production requires a separate design review before any true send, publish, delete, provision, or broad update action is enabled.
