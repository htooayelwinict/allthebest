# appv24 Workflow Guided Catalog Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a catalog-visible, approval-gated guided workflow run path so an end user can run catalog-backed workflows from the TUI without manually chaining internal phase commands.

**Architecture:** Add a green-zone capability catalog renderer and guided-run orchestration layer over the existing connector contract catalog, live plan, phase runner, approval ledger, readiness checks, and live transports. Keep connector interfaces owned by connector contracts/adapters; TUI and CLI only render and invoke workflow-layer APIs.

**Tech Stack:** Python dataclasses, existing appv24 workflow modules, pytest, current CLI/TUI slash-command handlers, existing live broker/transport abstractions.

---

## Zone Contract

Do not touch appv24 red-zone/kernel files:

```text
appV2.4/appv24/agent/
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/types.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
```

Preferred green-zone implementation files:

```text
appV2.4/appv24/workflows/capability_catalog.py
appV2.4/appv24/workflows/guided_run.py
appV2.4/appv24/workflows/live_readiness.py
appV2.4/appv24/workflows/__init__.py
appV2.4/tests/test_workflow_capability_catalog.py
appV2.4/tests/test_workflow_guided_run.py
appV2.4/tests/test_workflow_live_readiness.py
docs/appv24/workflow-run-live-path.md
```

Yellow-zone command exposure must stay thin and tested:

```text
appV2.4/appv24/cli.py
appV2.4/appv24/tui/interactive_mode.py
appV2.4/tests/test_cli.py
appV2.4/tests/test_tui.py
```

## File Structure

- `appV2.4/appv24/workflows/capability_catalog.py`: user-facing catalog view derived from `builtin_connector_contract_catalog()`.
- `appV2.4/appv24/workflows/guided_run.py`: guided execution report plus prepare/continue orchestration over existing phase APIs.
- `appV2.4/appv24/workflows/live_readiness.py`: add missing safe env hint for `telegram.broadcast_message`.
- `appV2.4/appv24/workflows/__init__.py`: export new workflow-layer types/functions.
- `appV2.4/appv24/cli.py`: add `/workflow-catalog`, `/workflow-run-guided`, and `/workflow-continue-guided` handlers.
- `appV2.4/appv24/tui/interactive_mode.py`: add the same user-facing commands with TUI rendering.
- `appV2.4/tests/test_workflow_capability_catalog.py`: catalog rendering and redaction coverage.
- `appV2.4/tests/test_workflow_guided_run.py`: orchestration behavior without real providers.
- `appV2.4/tests/test_cli.py`: command parsing/rendering coverage for CLI.
- `appV2.4/tests/test_tui.py`: command parsing/rendering coverage for TUI.
- `docs/appv24/workflow-run-live-path.md`: document guided user mode beside existing operator mode.

## Task 1: Capability Catalog View

**Files:**
- Create: `appV2.4/appv24/workflows/capability_catalog.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`
- Test: `appV2.4/tests/test_workflow_capability_catalog.py`

- [ ] **Step 1: Write failing catalog tests**

Create `appV2.4/tests/test_workflow_capability_catalog.py`:

```python
from __future__ import annotations

from appv24.workflows.capability_catalog import (
    build_workflow_capability_catalog,
    format_workflow_capability_catalog,
)


def test_capability_catalog_is_derived_from_connector_contracts() -> None:
    catalog = build_workflow_capability_catalog()
    by_action = {item.action_ref: item for item in catalog.capabilities}

    assert "brave.search" in by_action
    assert "telegram.list_subscribers" in by_action
    assert "telegram.broadcast_message" in by_action

    broadcast = by_action["telegram.broadcast_message"]
    assert broadcast.group == "Draft or send"
    assert broadcast.mode_labels == ("live-production",)
    assert broadcast.side_effect_class == "send"
    assert broadcast.approval_required is True
    assert broadcast.required_env_names == (
        "APPV24_TELEGRAM_ALLOWED_CHAT_IDS",
        "APPV24_TELEGRAM_BOT_TOKEN",
    )
    assert "recipients" in broadcast.input_fields
    assert "sent_count" in broadcast.output_fields


def test_capability_catalog_format_is_user_facing_and_redacted() -> None:
    rendered = format_workflow_capability_catalog(build_workflow_capability_catalog())

    assert "Workflow capability catalog" in rendered
    assert "Search web/news with Brave Search" in rendered
    assert "Broadcast Telegram message to registered subscribers" in rendered
    assert "telegram.broadcast_message" in rendered
    assert "approval: required" in rendered
    assert "env: APPV24_TELEGRAM_ALLOWED_CHAT_IDS, APPV24_TELEGRAM_BOT_TOKEN" in rendered
    assert "telegram-token" not in rendered
    assert "secret" not in rendered.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_capability_catalog.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.capability_catalog'
```

- [ ] **Step 3: Implement `capability_catalog.py`**

Create `appV2.4/appv24/workflows/capability_catalog.py`:

```python
"""User-facing workflow capability catalog derived from connector contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from appv24.workflows.connector_contracts import ConnectorContract, builtin_connector_contract_catalog, live_mode_values


ACTION_LABELS = {
    "brave.search": "Search web/news with Brave Search",
    "http.fetch": "Fetch an allowed URL",
    "source.fetch_document": "Fetch source document",
    "source.fetch_items": "Fetch source items",
    "telegram.list_subscribers": "List Telegram subscribers",
    "llm.summarize": "Summarize text/items",
    "llm.classify": "Classify text",
    "llm.extract_structured": "Extract structured fields",
    "policy.check": "Check deterministic policy",
    "gmail.create_draft": "Create Gmail draft",
    "notification.create_draft": "Create notification draft",
    "telegram.send_message": "Send Telegram message to one allowed chat",
    "telegram.broadcast_message": "Broadcast Telegram message to registered subscribers",
}

ACTION_GROUPS = {
    "brave.search": "Read data",
    "http.fetch": "Read data",
    "source.fetch_document": "Read data",
    "source.fetch_items": "Read data",
    "telegram.list_subscribers": "Read data",
    "llm.summarize": "Transform locally",
    "llm.classify": "Transform locally",
    "llm.extract_structured": "Transform locally",
    "policy.check": "Transform locally",
    "gmail.create_draft": "Draft or send",
    "notification.create_draft": "Draft or send",
    "telegram.create_message_draft": "Draft or send",
    "telegram.send_message": "Draft or send",
    "telegram.broadcast_message": "Draft or send",
}

GROUP_ORDER = ("Read data", "Transform locally", "Draft or send", "Other")


@dataclass(frozen=True)
class WorkflowCapability:
    group: str
    label: str
    action_ref: str
    input_fields: tuple[str, ...]
    required_input_fields: tuple[str, ...]
    output_fields: tuple[str, ...]
    mode_labels: tuple[str, ...]
    side_effect_class: str
    approval_required: bool
    required_env_names: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowCapabilityCatalog:
    capabilities: tuple[WorkflowCapability, ...]


def build_workflow_capability_catalog(
    contract_catalog: Mapping[str, ConnectorContract] | None = None,
) -> WorkflowCapabilityCatalog:
    contracts = builtin_connector_contract_catalog() if contract_catalog is None else contract_catalog
    capabilities = tuple(
        _capability(action_ref, contract)
        for action_ref, contract in sorted(contracts.items(), key=lambda item: (_group_sort_key(item[0]), item[0]))
    )
    return WorkflowCapabilityCatalog(capabilities=capabilities)


def format_workflow_capability_catalog(catalog: WorkflowCapabilityCatalog) -> str:
    lines = ["Workflow capability catalog"]
    for group in GROUP_ORDER:
        items = tuple(item for item in catalog.capabilities if item.group == group)
        if not items:
            continue
        lines.append("")
        lines.append(group)
        for item in items:
            approval = "required" if item.approval_required else "not required"
            modes = ", ".join(item.mode_labels)
            inputs = ", ".join(item.input_fields) if item.input_fields else "none"
            outputs = ", ".join(item.output_fields) if item.output_fields else "none"
            env = ", ".join(item.required_env_names) if item.required_env_names else "none"
            lines.append(f"- {item.label} ({item.action_ref})")
            lines.append(f"  modes: {modes}; side effect: {item.side_effect_class}; approval: {approval}")
            lines.append(f"  inputs: {inputs}")
            lines.append(f"  outputs: {outputs}")
            lines.append(f"  env: {env}")
    return "\n".join(lines)


def _capability(action_ref: str, contract: ConnectorContract) -> WorkflowCapability:
    return WorkflowCapability(
        group=ACTION_GROUPS.get(action_ref, "Other"),
        label=ACTION_LABELS.get(action_ref, action_ref),
        action_ref=action_ref,
        input_fields=_input_fields(contract),
        required_input_fields=contract.required_input_fields,
        output_fields=contract.allowed_outputs,
        mode_labels=live_mode_values(contract) or ("local",),
        side_effect_class=contract.side_effect_class.value,
        approval_required=contract.approval_required,
        required_env_names=_required_env_names(contract),
    )


def _input_fields(contract: ConnectorContract) -> tuple[str, ...]:
    properties = contract.input_schema.get("properties", {})
    if not isinstance(properties, dict):
        return contract.required_input_fields
    return tuple(sorted(str(name) for name in properties))


def _required_env_names(contract: ConnectorContract) -> tuple[str, ...]:
    names = {secret.env_name for secret in contract.secrets if secret.required}
    if contract.target_policy.allow_env:
        names.add(contract.target_policy.allow_env)
    if contract.target_policy.allowed_chat_ids_env:
        names.add(contract.target_policy.allowed_chat_ids_env)
    return tuple(sorted(names))


def _group_sort_key(action_ref: str) -> tuple[int, str]:
    group = ACTION_GROUPS.get(action_ref, "Other")
    try:
        group_index = GROUP_ORDER.index(group)
    except ValueError:
        group_index = len(GROUP_ORDER)
    return group_index, action_ref
```

- [ ] **Step 4: Export catalog types/functions**

Modify `appV2.4/appv24/workflows/__init__.py` imports:

```python
from appv24.workflows.capability_catalog import (
    WorkflowCapability,
    WorkflowCapabilityCatalog,
    build_workflow_capability_catalog,
    format_workflow_capability_catalog,
)
```

Add these names to `__all__`:

```python
"WorkflowCapability",
"WorkflowCapabilityCatalog",
"build_workflow_capability_catalog",
"format_workflow_capability_catalog",
```

- [ ] **Step 5: Run catalog tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_capability_catalog.py -q
```

Expected:

```text
2 passed
```

- [ ] **Step 6: Commit catalog view**

Run:

```bash
git add appV2.4/appv24/workflows/capability_catalog.py appV2.4/appv24/workflows/__init__.py appV2.4/tests/test_workflow_capability_catalog.py
git commit -m "feat(appv24): render workflow capability catalog"
```

Expected:

```text
One commit containing only workflow green-zone files and tests.
```

## Task 2: Guided Run Orchestrator

**Files:**
- Create: `appV2.4/appv24/workflows/guided_run.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`
- Test: `appV2.4/tests/test_workflow_guided_run.py`

- [ ] **Step 1: Write failing guided-run tests**

Create `appV2.4/tests/test_workflow_guided_run.py` with focused tests. Use existing fake broker patterns from `appV2.4/tests/test_workflow_phase_runner.py`.

```python
from __future__ import annotations

from pathlib import Path

import pytest

from appv24.workflows.approval_gate import ApprovalDecision
from appv24.workflows.broker import BrokerRequest, BrokerResult, LocalWorkflowBroker
from appv24.workflows.credential_refs import CredentialRefResolver, CredentialScope
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.guided_run import (
    continue_guided_workflow_run,
    format_guided_workflow_run_report,
    prepare_guided_workflow_run,
)
from appv24.workflows.live_broker import LiveWorkflowBroker, LiveWorkflowTransport
from appv24.workflows.run_store import WorkflowRunStore


class _ReadTransport(LiveWorkflowTransport):
    def invoke(self, request: BrokerRequest) -> BrokerResult:
        if request.connector_action == "brave.search":
            return BrokerResult(
                status="live-read",
                provider_id="fake-brave",
                outputs={"items": [{"title": "AI model news"}], "source_count": 1, "query": "AI news"},
            )
        if request.connector_action == "telegram.list_subscribers":
            return BrokerResult(
                status="live-read",
                provider_id="fake-telegram",
                outputs={"recipients": ["admin-chat"], "recipient_count": 1, "audience_hash": "aud-1"},
            )
        raise AssertionError(f"unexpected read action {request.connector_action}")


class _ProductionTransport(LiveWorkflowTransport):
    def __init__(self) -> None:
        self.requests: list[BrokerRequest] = []

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        self.requests.append(request)
        return BrokerResult(
            status="live-production",
            provider_id="fake-telegram",
            outputs={"sent_count": 1, "message_ids": ["msg-1"], "chat_ids": ["admin-chat"]},
        )


def test_prepare_guided_workflow_run_stops_at_approval(tmp_path: Path) -> None:
    package = _write_ai_news_broadcast_package(tmp_path)
    report = prepare_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=package / ".workflow-runs",
        ),
        live_read_broker=LiveWorkflowBroker(
            {
                "brave.search": _ReadTransport(),
                "telegram.list_subscribers": _ReadTransport(),
            },
            mode="live-read",
        ),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "telegram": CredentialScope(available=True, scopes=("telegram:subscribers:read", "telegram:send")),
                "llm": CredentialScope(available=True, scopes=()),
            }
        ),
        local_broker=LocalWorkflowBroker(
            {
                "llm.summarize": lambda request: BrokerResult(
                    status="simulated",
                    provider_id="fake-llm",
                    outputs={"summary": "AI digest"},
                )
            }
        ),
    )

    assert report.status == "approval-required"
    assert report.live_plan_status == "partial-live"
    assert report.live_read_run_id.startswith("run-")
    assert report.approval_run_id == report.local_run_id
    assert len(report.approval_request_ids) == 1
    rendered = format_guided_workflow_run_report(report)
    assert "Workflow guided run: approval-required" in rendered
    assert "next action: approve external side effect" in rendered


def test_continue_guided_workflow_run_refuses_unapproved_request(tmp_path: Path) -> None:
    package = _write_ai_news_broadcast_package(tmp_path)
    prepare_report = _prepare_package_to_approval(package)

    report = continue_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=package / ".workflow-runs",
        ),
        approval_run_id=prepare_report.approval_run_id or "",
        live_production_broker=LiveWorkflowBroker({}, mode="live-production", production_enabled=True),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
        env={},
    )

    assert report.status == "approval-required"
    assert report.live_production_run_id == ""
    assert report.next_action == "approve external side effect"


def test_continue_guided_workflow_run_runs_readiness_then_production(tmp_path: Path) -> None:
    package = _write_ai_news_broadcast_package(tmp_path)
    prepare_report = _prepare_package_to_approval(package)
    assert prepare_report.approval_run_id is not None

    store = WorkflowRunStore(package / ".workflow-runs")
    approval = store.read_approval_requests(prepare_report.approval_run_id)[0]
    local_report = store.read_run_report(prepare_report.approval_run_id)
    store.record_approval_decision(
        prepare_report.approval_run_id,
        approval.approval_request_id,
        ApprovalDecision(
            approved=True,
            package_fingerprint=str(local_report["package_fingerprint"]),
            decided_by="test",
        ),
    )
    transport = _ProductionTransport()

    report = continue_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=package / ".workflow-runs",
            operator_env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat"},
        ),
        approval_run_id=prepare_report.approval_run_id,
        live_production_broker=LiveWorkflowBroker(
            {"telegram.broadcast_message": transport},
            mode="live-production",
            production_enabled=True,
        ),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
        env={
            "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED": "true",
            "APPV24_TELEGRAM_BOT_TOKEN": "redacted-token",
            "APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat",
            "APPV24_TELEGRAM_CREDENTIAL_REF": "telegram-ref",
            "APPV24_TELEGRAM_SCOPES": "telegram:send",
        },
        readiness_inspector=lambda request, env: "ready",
    )

    assert report.status == "passed"
    assert report.readiness_status == "ready"
    assert report.live_production_run_id.startswith("run-")
    assert transport.requests[0].connector_action == "telegram.broadcast_message"


def _prepare_package_to_approval(package: Path):
    return prepare_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=package / ".workflow-runs",
        ),
        live_read_broker=LiveWorkflowBroker(
            {
                "brave.search": _ReadTransport(),
                "telegram.list_subscribers": _ReadTransport(),
            },
            mode="live-read",
        ),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "telegram": CredentialScope(available=True, scopes=("telegram:subscribers:read", "telegram:send")),
                "llm": CredentialScope(available=True, scopes=()),
            }
        ),
        local_broker=LocalWorkflowBroker(
            {
                "llm.summarize": lambda request: BrokerResult(
                    status="simulated",
                    provider_id="fake-llm",
                    outputs={"summary": "AI digest"},
                )
            }
        ),
    )
```

Add a package helper at the bottom of the same test file:

```python
def _write_ai_news_broadcast_package(root: Path) -> Path:
    package = root / "ai-news-telegram"
    (package / "dry_run_fixtures").mkdir(parents=True)
    (package / "workflow.yaml").write_text(
        """version: appv24.workflow/v1
name: ai-news-telegram
description: Search AI news and broadcast digest to registered Telegram subscribers.
design:
  intent: AI news Telegram digest
  trigger: manual
  inputs: []
  outputs: [telegram digest]
  assumptions: []
  side_effects: [telegram broadcast]
  approval_gates: [approve_telegram_broadcast]
  failure_modes: []
  rollback: manual follow-up
triggers:
  - id: manual
    type: manual
entrypoint: search_ai_news
steps:
  - id: search_ai_news
    uses: brave.search
    with:
      query: artificial intelligence news today
      freshness: day
      count: 5
    next: list_subscribers
  - id: list_subscribers
    uses: telegram.list_subscribers
    with:
      audience: default
    next: summarize_digest
  - id: summarize_digest
    uses: llm.summarize
    with:
      items: "{{ steps.search_ai_news.outputs.items }}"
      output_contract: summary_v1
    next: send_digest
  - id: send_digest
    uses: telegram.broadcast_message
    approval: approve_telegram_broadcast
    with:
      recipients: "{{ steps.list_subscribers.outputs.recipients }}"
      audience_hash: "{{ steps.list_subscribers.outputs.audience_hash }}"
      text: "{{ steps.summarize_digest.outputs.summary }}"
    next: done
  - id: done
    type: terminal
""",
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        """version: appv24.connectors/v1
connectors:
  - id: brave
    adapter: direct_api
    actions:
      - id: search
        side_effect: false
        outputs:
          allowed: [items, source_count, query, artifact_path]
  - id: telegram
    adapter: direct_api
    actions:
      - id: list_subscribers
        side_effect: false
        outputs:
          allowed: [recipients, recipient_count, audience_hash, artifact_path]
      - id: broadcast_message
        side_effect: true
        side_effect_class: send
        outputs:
          allowed: [sent_count, message_ids, chat_ids, artifact_path]
  - id: llm
    adapter: llm
    actions:
      - id: summarize
        side_effect: false
        outputs:
          allowed: [summary, citations, confidence, raw_response, artifact_path]
""",
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        """version: appv24.permissions/v1
permissions:
  - connector: brave
    actions: [search]
    scopes: [brave:search]
  - connector: telegram
    actions: [list_subscribers, broadcast_message]
    scopes: [telegram:subscribers:read, telegram:send]
  - connector: llm
    actions: [summarize]
    scopes: []
approvals:
  - id: approve_telegram_broadcast
    mode: explicit
    required_for: [send_digest]
""",
        encoding="utf-8",
    )
    (package / "dry_run_fixtures" / "happy_path.yaml").write_text(
        """input: {}
steps: {}
""",
        encoding="utf-8",
    )
    (package / "dry_run_fixtures" / "rejection_path.yaml").write_text(
        """input: {}
steps: {}
""",
        encoding="utf-8",
    )
    (package / "rollback.md").write_text("Manual operator follow-up if Telegram delivery fails.\\n", encoding="utf-8")
    return package
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_guided_run.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.guided_run'
```

- [ ] **Step 3: Implement `guided_run.py`**

Create `appV2.4/appv24/workflows/guided_run.py`:

```python
"""Guided user-mode orchestration over existing workflow live phases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from appv24.workflows.approval_commands import list_workflow_approval_requests
from appv24.workflows.broker import LocalWorkflowBroker
from appv24.workflows.connector_contracts import ConnectorLiveMode, builtin_connector_contract_catalog, workflow_operator_env_from_env
from appv24.workflows.credential_refs import CredentialRefResolver
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.live_broker import LiveWorkflowBroker
from appv24.workflows.live_plan import build_workflow_live_plan_from_path
from appv24.workflows.live_readiness import inspect_workflow_live_readiness
from appv24.workflows.phase_runner import (
    WorkflowPhaseRunReport,
    run_workflow_approved_live_production,
    run_workflow_live_read_to_approval,
)


@dataclass(frozen=True)
class GuidedWorkflowRunReport:
    status: str
    package_path: Path
    fixture_name: str
    live_plan_status: str = ""
    live_read_run_id: str = ""
    local_run_id: str = ""
    approval_run_id: str | None = None
    approval_request_ids: tuple[str, ...] = ()
    readiness_status: str = ""
    live_production_run_id: str = ""
    issues: tuple[str, ...] = ()
    next_action: str = ""


ReadinessInspector = Callable[[WorkflowRunRequest, Mapping[str, str]], object]


def prepare_guided_workflow_run(
    request: WorkflowRunRequest,
    *,
    live_read_broker: LiveWorkflowBroker,
    credential_resolver: CredentialRefResolver,
    local_broker: LocalWorkflowBroker | None = None,
) -> GuidedWorkflowRunReport:
    package = Path(request.package_path).expanduser().resolve()
    fixture_name = request.fixture_name or "happy_path.yaml"
    plan = build_workflow_live_plan_from_path(package, fixture_name)
    if plan.status == "blocked":
        return _report(
            status="blocked",
            package_path=package,
            fixture_name=fixture_name,
            live_plan_status=plan.status,
            issues=plan.issues,
            next_action="fix workflow package",
        )
    allowlist = _allowlist_from_command(plan.next_commands, "/workflow-run-live-read-to-approval")
    if not allowlist:
        return _report(
            status="blocked",
            package_path=package,
            fixture_name=fixture_name,
            live_plan_status=plan.status,
            issues=("guided run requires a live-read-to-approval phase",),
            next_action="inspect live plan",
        )

    phase_report = run_workflow_live_read_to_approval(
        WorkflowRunRequest(
            package_path=package,
            fixture_name=fixture_name,
            mode=WorkflowRunMode.LIVE_READ,
            run_root=request.run_root,
            connector_allowlist=allowlist,
            activation_id=request.activation_id,
            activation_trigger_type=request.activation_trigger_type,
            operator_env=request.operator_env,
        ),
        live_read_broker=live_read_broker,
        credential_resolver=credential_resolver,
        local_broker=local_broker,
    )
    return _from_phase_report(package, fixture_name, plan.status, phase_report)


def continue_guided_workflow_run(
    request: WorkflowRunRequest,
    *,
    approval_run_id: str,
    live_production_broker: LiveWorkflowBroker,
    credential_resolver: CredentialRefResolver,
    env: Mapping[str, str],
    readiness_inspector: ReadinessInspector | None = None,
) -> GuidedWorkflowRunReport:
    package = Path(request.package_path).expanduser().resolve()
    fixture_name = request.fixture_name or "happy_path.yaml"
    approvals = list_workflow_approval_requests(package, approval_run_id)
    pending = tuple(item.approval_request_id for item in approvals.items if item.status != "approved")
    if pending:
        return _report(
            status="approval-required",
            package_path=package,
            fixture_name=fixture_name,
            approval_run_id=approval_run_id,
            approval_request_ids=pending,
            next_action="approve external side effect",
        )

    production_actions = _production_actions_for_approvals(approvals.items)
    if not production_actions:
        return _report(
            status="blocked",
            package_path=package,
            fixture_name=fixture_name,
            approval_run_id=approval_run_id,
            issues=("no approved live-production connector actions found",),
            next_action="inspect approvals",
        )

    production_request = WorkflowRunRequest(
        package_path=package,
        fixture_name=fixture_name,
        mode=WorkflowRunMode.LIVE_PRODUCTION,
        run_root=request.run_root,
        approval_run_id=approval_run_id,
        connector_allowlist=production_actions,
        activation_id=request.activation_id,
        activation_trigger_type=request.activation_trigger_type,
        operator_env=request.operator_env or workflow_operator_env_from_env(env),
    )
    readiness = (readiness_inspector or _inspect_readiness)(production_request, env)
    readiness_status = readiness if isinstance(readiness, str) else str(getattr(readiness, "status", "blocked"))
    readiness_issues = () if isinstance(readiness, str) else tuple(getattr(readiness, "issues", ()))
    if readiness_status != "ready":
        return _report(
            status="blocked",
            package_path=package,
            fixture_name=fixture_name,
            approval_run_id=approval_run_id,
            readiness_status=readiness_status,
            issues=readiness_issues,
            next_action="fix readiness issues",
        )

    phase_report = run_workflow_approved_live_production(
        production_request,
        approval_run_id=approval_run_id,
        live_production_broker=live_production_broker,
        credential_resolver=credential_resolver,
    )
    return _report(
        status=phase_report.status,
        package_path=package,
        fixture_name=fixture_name,
        approval_run_id=approval_run_id,
        readiness_status=readiness_status,
        live_production_run_id=phase_report.live_production_run_id,
        issues=phase_report.issues,
        next_action="" if phase_report.status == "passed" else "inspect live-production report",
    )


def format_guided_workflow_run_report(report: GuidedWorkflowRunReport) -> str:
    lines = [
        f"Workflow guided run: {report.status}",
        f"package: {report.package_path}",
        f"fixture: {report.fixture_name}",
    ]
    if report.live_plan_status:
        lines.append(f"live plan: {report.live_plan_status}")
    if report.live_read_run_id:
        lines.append(f"live-read run: {report.live_read_run_id}")
    if report.local_run_id:
        lines.append(f"local run: {report.local_run_id}")
    if report.approval_run_id:
        lines.append(f"approval run: {report.approval_run_id}")
    if report.approval_request_ids:
        lines.append("approval requests:")
        lines.extend(f"- {request_id}" for request_id in report.approval_request_ids)
    if report.readiness_status:
        lines.append(f"readiness: {report.readiness_status}")
    if report.live_production_run_id:
        lines.append(f"live-production run: {report.live_production_run_id}")
    if report.issues:
        lines.append("issues:")
        lines.extend(f"- {issue}" for issue in report.issues)
    if report.next_action:
        lines.append(f"next action: {report.next_action}")
    return "\n".join(lines)


def _from_phase_report(
    package: Path,
    fixture_name: str,
    live_plan_status: str,
    phase_report: WorkflowPhaseRunReport,
) -> GuidedWorkflowRunReport:
    next_action = "approve external side effect" if phase_report.status == "approval-required" else ""
    if phase_report.status not in {"passed", "approval-required"} and not next_action:
        next_action = "inspect phase report"
    return _report(
        status=phase_report.status,
        package_path=package,
        fixture_name=fixture_name,
        live_plan_status=live_plan_status,
        live_read_run_id=phase_report.live_read_run_id,
        local_run_id=phase_report.local_run_id,
        approval_run_id=phase_report.approval_run_id,
        approval_request_ids=phase_report.approval_request_ids,
        issues=phase_report.issues,
        next_action=next_action,
    )


def _allowlist_from_command(commands: tuple[str, ...], command_name: str) -> tuple[str, ...]:
    prefix = f"{command_name} "
    for command in commands:
        if not command.startswith(prefix) or " --allow " not in command:
            continue
        _, allow = command.rsplit(" --allow ", 1)
        return tuple(action.strip() for action in allow.split(",") if action.strip())
    return ()


def _production_actions_for_approvals(items) -> tuple[str, ...]:
    contracts = builtin_connector_contract_catalog()
    actions: list[str] = []
    for item in items:
        contract = contracts.get(item.connector_action)
        if contract is None or ConnectorLiveMode.LIVE_PRODUCTION not in contract.live_modes:
            continue
        if item.connector_action not in actions:
            actions.append(item.connector_action)
    return tuple(actions)


def _inspect_readiness(request: WorkflowRunRequest, env: Mapping[str, str]):
    return inspect_workflow_live_readiness(request, env=env)


def _report(**kwargs) -> GuidedWorkflowRunReport:
    return GuidedWorkflowRunReport(**kwargs)
```

- [ ] **Step 4: Export guided-run types/functions**

Modify `appV2.4/appv24/workflows/__init__.py` imports:

```python
from appv24.workflows.guided_run import (
    GuidedWorkflowRunReport,
    continue_guided_workflow_run,
    format_guided_workflow_run_report,
    prepare_guided_workflow_run,
)
```

Add these names to `__all__`:

```python
"GuidedWorkflowRunReport",
"continue_guided_workflow_run",
"format_guided_workflow_run_report",
"prepare_guided_workflow_run",
```

- [ ] **Step 5: Run guided-run tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_guided_run.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 6: Commit guided orchestrator**

Run:

```bash
git add appV2.4/appv24/workflows/guided_run.py appV2.4/appv24/workflows/__init__.py appV2.4/tests/test_workflow_guided_run.py
git commit -m "feat(appv24): add guided workflow run orchestration"
```

Expected:

```text
One commit containing workflow green-zone implementation and tests.
```

## Task 3: Production Readiness Hint For Broadcast

**Files:**
- Modify: `appV2.4/appv24/workflows/live_readiness.py`
- Test: `appV2.4/tests/test_workflow_live_readiness.py`

- [ ] **Step 1: Add failing readiness test**

Append to `appV2.4/tests/test_workflow_live_readiness.py`:

```python
def test_live_readiness_reports_telegram_broadcast_transport_env(tmp_path: Path) -> None:
    package = _write_telegram_broadcast_package(tmp_path)
    report = inspect_workflow_live_readiness(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            approval_run_id="run-approval",
            connector_allowlist=("telegram.broadcast_message",),
        ),
        env={},
    )

    rendered = format_workflow_live_readiness_report(report)
    assert (
        "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, "
        "APPV24_TELEGRAM_BOT_TOKEN, APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
    ) in rendered
```

Add this helper to the bottom of `appV2.4/tests/test_workflow_live_readiness.py`:

```python
def _write_telegram_broadcast_package(root: Path) -> Path:
    package = root / "telegram-broadcast-readiness"
    (package / "dry_run_fixtures").mkdir(parents=True)
    (package / "workflow.yaml").write_text(
        """version: appv24.workflow/v1
name: telegram-broadcast-readiness
description: Broadcast a prepared message to Telegram subscribers.
design:
  intent: readiness test
  trigger: manual
  inputs: []
  outputs: []
  assumptions: []
  side_effects: [telegram broadcast]
  approval_gates: [approve_send]
  failure_modes: []
  rollback: manual follow-up
triggers:
  - id: manual
    type: manual
entrypoint: send_digest
steps:
  - id: send_digest
    uses: telegram.broadcast_message
    approval: approve_send
    with:
      recipients: [admin-chat]
      audience_hash: aud-1
      text: digest
    next: done
  - id: done
    type: terminal
""",
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        """version: appv24.connectors/v1
connectors:
  - id: telegram
    adapter: direct_api
    actions:
      - id: broadcast_message
        side_effect: true
        side_effect_class: send
        outputs:
          allowed: [sent_count, message_ids, chat_ids, artifact_path]
""",
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        """version: appv24.permissions/v1
permissions:
  - connector: telegram
    actions: [broadcast_message]
    scopes: [telegram:send]
approvals:
  - id: approve_send
    mode: explicit
    required_for: [send_digest]
""",
        encoding="utf-8",
    )
    (package / "dry_run_fixtures" / "happy_path.yaml").write_text("input: {}\\nsteps: {}\\n", encoding="utf-8")
    (package / "rollback.md").write_text("Manual operator follow-up.\\n", encoding="utf-8")
    return package
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_readiness.py::test_live_readiness_reports_telegram_broadcast_transport_env -q
```

Expected:

```text
AssertionError
```

- [ ] **Step 3: Add broadcast hint**

Modify `_transport_env_hint()` in `appV2.4/appv24/workflows/live_readiness.py`:

```python
        (
            WorkflowRunMode.LIVE_PRODUCTION,
            "telegram.broadcast_message",
        ): (
            "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, "
            "APPV24_TELEGRAM_BOT_TOKEN, APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
        ),
```

Keep the existing `telegram.send_message` hint unchanged.

- [ ] **Step 4: Run readiness tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_readiness.py -q
```

Expected:

```text
All selected tests pass.
```

- [ ] **Step 5: Commit readiness hint**

Run:

```bash
git add appV2.4/appv24/workflows/live_readiness.py appV2.4/tests/test_workflow_live_readiness.py
git commit -m "fix(appv24): show telegram broadcast readiness env"
```

Expected:

```text
One small workflow green-zone commit.
```

## Task 4: CLI Guided Commands

**Files:**
- Modify: `appV2.4/appv24/cli.py`
- Test: `appV2.4/tests/test_cli.py`

- [ ] **Step 1: Add failing CLI catalog test**

Append to `appV2.4/tests/test_cli.py` near workflow command tests:

```python
def test_cli_workflow_catalog_command_renders_user_catalog(monkeypatch, tmp_path, capsys) -> None:
    _disable_real_startup_live_catalog_fetch(monkeypatch)
    prompts = iter(["/workflow-catalog", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(prompts))

    cli.main(["--cwd", str(tmp_path)])

    captured = capsys.readouterr()
    assert "Workflow capability catalog" in captured.out
    assert "Search web/news with Brave Search" in captured.out
    assert "telegram.broadcast_message" in captured.out
```

- [ ] **Step 2: Add failing CLI guided command tests**

Append:

```python
def test_cli_workflow_run_guided_command_invokes_prepare(monkeypatch, tmp_path, capsys) -> None:
    _disable_real_startup_live_catalog_fetch(monkeypatch)
    package = tmp_path / "pkg"
    package.mkdir()
    prompts = iter([f"/workflow-run-guided {package} happy_path.yaml", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(prompts))

    def fake_prepare(request, **kwargs):
        return GuidedWorkflowRunReport(
            status="approval-required",
            package_path=Path(request.package_path),
            fixture_name=request.fixture_name or "happy_path.yaml",
            live_read_run_id="run-read",
            local_run_id="run-local",
            approval_run_id="run-local",
            approval_request_ids=("approval-1",),
            next_action="approve external side effect",
        )

    monkeypatch.setattr(cli, "prepare_guided_workflow_run", fake_prepare, raising=False)

    cli.main(["--cwd", str(tmp_path)])

    captured = capsys.readouterr()
    assert "Workflow guided run: approval-required" in captured.out
    assert "approval-1" in captured.out


def test_cli_workflow_continue_guided_command_invokes_continue(monkeypatch, tmp_path, capsys) -> None:
    _disable_real_startup_live_catalog_fetch(monkeypatch)
    package = tmp_path / "pkg"
    package.mkdir()
    prompts = iter([f"/workflow-continue-guided {package} run-local", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(prompts))

    def fake_continue(request, **kwargs):
        return GuidedWorkflowRunReport(
            status="passed",
            package_path=Path(request.package_path),
            fixture_name=request.fixture_name or "happy_path.yaml",
            approval_run_id=kwargs["approval_run_id"],
            readiness_status="ready",
            live_production_run_id="run-prod",
        )

    monkeypatch.setattr(cli, "continue_guided_workflow_run", fake_continue, raising=False)

    cli.main(["--cwd", str(tmp_path)])

    captured = capsys.readouterr()
    assert "Workflow guided run: passed" in captured.out
    assert "live-production run: run-prod" in captured.out
```

Add these imports at the top of `appV2.4/tests/test_cli.py`:

```python
from pathlib import Path

from appv24.workflows import GuidedWorkflowRunReport
```

- [ ] **Step 3: Run CLI tests to verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_cli.py::test_cli_workflow_catalog_command_renders_user_catalog \
  appV2.4/tests/test_cli.py::test_cli_workflow_run_guided_command_invokes_prepare \
  appV2.4/tests/test_cli.py::test_cli_workflow_continue_guided_command_invokes_continue -q
```

Expected:

```text
At least one selected test fails because the commands are not wired.
```

- [ ] **Step 4: Add CLI imports**

Modify `appV2.4/appv24/cli.py` imports:

```python
from appv24.workflows import (
    build_workflow_capability_catalog,
    continue_guided_workflow_run,
    format_guided_workflow_run_report,
    format_workflow_capability_catalog,
    prepare_guided_workflow_run,
)
```

Keep a single import source for each added name in `cli.py`; add the names to the current workflow import block used by adjacent workflow command handlers.

- [ ] **Step 5: Add CLI dispatch**

In both prompt-dispatch blocks around existing workflow commands, insert before `/workflow-live-plan`:

```python
        if _handle_workflow_catalog_command(app, prompt):
            continue
        if _handle_workflow_run_guided_command(app, prompt):
            continue
        if _handle_workflow_continue_guided_command(app, prompt):
            continue
```

- [ ] **Step 6: Add CLI handlers**

Add near `_handle_workflow_live_plan_command()`:

```python
def _handle_workflow_catalog_command(app: CodingApp, prompt: str) -> bool:
    if prompt.strip() != "/workflow-catalog":
        return False
    print(format_workflow_capability_catalog(build_workflow_capability_catalog()))
    return True


def _handle_workflow_run_guided_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_run_guided_command(prompt):
        return False
    args = prompt.split()
    if len(args) not in {2, 3}:
        print("Workflow guided run failed: expected /workflow-run-guided <path> [fixture]")
        return True
    try:
        package_path = _resolve_workflow_package_path(app.cwd, args[1])
        fixture_name = args[2] if len(args) == 3 else "happy_path.yaml"
        report = prepare_guided_workflow_run(
            WorkflowRunRequest(
                package_path=package_path,
                fixture_name=fixture_name,
                mode=WorkflowRunMode.LIVE_READ,
                operator_env=workflow_operator_env_from_env(os.environ),
            ),
            live_read_broker=LiveWorkflowBroker(build_live_read_transports_from_env(os.environ), mode="live-read"),
            credential_resolver=credential_resolver_for_package_env(package_path, os.environ),
        )
        print(format_guided_workflow_run_report(report))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow guided run failed: {error}")
    return True


def _handle_workflow_continue_guided_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_continue_guided_command(prompt):
        return False
    args = prompt.split()
    if len(args) not in {3, 4}:
        print("Workflow guided continue failed: expected /workflow-continue-guided <path> <approval-run-id> [fixture]")
        return True
    try:
        package_path = _resolve_workflow_package_path(app.cwd, args[1])
        approval_run_id = args[2]
        fixture_name = args[3] if len(args) == 4 else "happy_path.yaml"
        report = continue_guided_workflow_run(
            WorkflowRunRequest(
                package_path=package_path,
                fixture_name=fixture_name,
                mode=WorkflowRunMode.LIVE_PRODUCTION,
                operator_env=workflow_operator_env_from_env(os.environ),
            ),
            approval_run_id=approval_run_id,
            live_production_broker=LiveWorkflowBroker(
                build_live_production_transports_from_env(os.environ),
                mode="live-production",
                production_enabled=True,
            ),
            credential_resolver=credential_resolver_for_package_env(package_path, os.environ),
            env=os.environ,
        )
        print(format_guided_workflow_run_report(report))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow guided continue failed: {error}")
    return True
```

Add command predicates near other `_is_workflow_*` helpers:

```python
def _is_workflow_run_guided_command(prompt: str) -> bool:
    return prompt == "/workflow-run-guided" or prompt.startswith("/workflow-run-guided ")


def _is_workflow_continue_guided_command(prompt: str) -> bool:
    return prompt == "/workflow-continue-guided" or prompt.startswith("/workflow-continue-guided ")
```

- [ ] **Step 7: Run CLI command tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_cli.py::test_cli_workflow_catalog_command_renders_user_catalog \
  appV2.4/tests/test_cli.py::test_cli_workflow_run_guided_command_invokes_prepare \
  appV2.4/tests/test_cli.py::test_cli_workflow_continue_guided_command_invokes_continue -q
```

Expected:

```text
3 passed
```

- [ ] **Step 8: Commit CLI surface**

Run:

```bash
git add appV2.4/appv24/cli.py appV2.4/tests/test_cli.py
git commit -m "feat(appv24): expose guided workflow run in cli"
```

Expected:

```text
One yellow-zone CLI commit with focused tests.
```

## Task 5: TUI Guided Commands

**Files:**
- Modify: `appV2.4/appv24/tui/interactive_mode.py`
- Test: `appV2.4/tests/test_tui.py`

- [ ] **Step 1: Add failing TUI catalog test**

Append near existing workflow TUI command tests:

```python
def test_tui_workflow_catalog_command_renders_user_catalog(tmp_path, monkeypatch) -> None:
    inputs = iter(["/workflow-catalog", "/exit"])
    terminal = FakeTerminal(columns=160, rows=80)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.profile_id = "workflow-designer-lite"
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    assert mode.run() == 0

    rendered = strip_ansi("\n".join(app.tui.render(160)))

    assert "Workflow capability catalog" in rendered
    assert "Search web/news with Brave Search" in rendered
    assert "telegram.broadcast_message" in rendered
```

- [ ] **Step 2: Add failing TUI guided command tests**

Append:

```python
def test_tui_workflow_run_guided_command_renders_report(tmp_path, monkeypatch) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    inputs = iter([f"/workflow-run-guided {package} happy_path.yaml", "/exit"])

    def fake_prepare(request, **kwargs):
        return GuidedWorkflowRunReport(
            status="approval-required",
            package_path=Path(request.package_path),
            fixture_name=request.fixture_name or "happy_path.yaml",
            approval_run_id="run-local",
            approval_request_ids=("approval-1",),
            next_action="approve external side effect",
        )

    monkeypatch.setattr(interactive_mode, "prepare_guided_workflow_run", fake_prepare, raising=False)

    terminal = FakeTerminal(columns=160, rows=80)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.profile_id = "workflow-designer-lite"
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    assert mode.run() == 0

    rendered = strip_ansi("\n".join(app.tui.render(160)))

    assert "Workflow guided run approval-required" in rendered
    assert "approval-1" in rendered


def test_tui_workflow_continue_guided_command_renders_report(tmp_path, monkeypatch) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    inputs = iter([f"/workflow-continue-guided {package} run-local", "/exit"])

    def fake_continue(request, **kwargs):
        return GuidedWorkflowRunReport(
            status="passed",
            package_path=Path(request.package_path),
            fixture_name=request.fixture_name or "happy_path.yaml",
            approval_run_id=kwargs["approval_run_id"],
            readiness_status="ready",
            live_production_run_id="run-prod",
        )

    monkeypatch.setattr(interactive_mode, "continue_guided_workflow_run", fake_continue, raising=False)

    terminal = FakeTerminal(columns=160, rows=80)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.profile_id = "workflow-designer-lite"
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    assert mode.run() == 0

    rendered = strip_ansi("\n".join(app.tui.render(160)))

    assert "Workflow guided continue passed" in rendered
    assert "live-production run: run-prod" in rendered
```

Add these imports at the top of `appV2.4/tests/test_tui.py`:

```python
from pathlib import Path

from appv24.workflows import GuidedWorkflowRunReport
from appv24.tui import interactive_mode
```

- [ ] **Step 3: Run TUI tests to verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_tui.py::test_tui_workflow_catalog_command_renders_user_catalog \
  appV2.4/tests/test_tui.py::test_tui_workflow_run_guided_command_renders_report \
  appV2.4/tests/test_tui.py::test_tui_workflow_continue_guided_command_renders_report -q
```

Expected:

```text
At least one selected test fails because the TUI commands are not wired.
```

- [ ] **Step 4: Add TUI imports**

Modify `appV2.4/appv24/tui/interactive_mode.py` imports by adding these names to the current workflow import block:

```python
from appv24.workflows import (
    build_workflow_capability_catalog,
    continue_guided_workflow_run,
    format_guided_workflow_run_report,
    format_workflow_capability_catalog,
    prepare_guided_workflow_run,
)
```

- [ ] **Step 5: Add command palette/help entries**

Add command entries near existing workflow commands:

```python
{"name": "workflow-catalog", "description": "Show available workflow capabilities"},
{"name": "workflow-run-guided", "description": "Run live-read to approval as a guided user flow"},
{"name": "workflow-continue-guided", "description": "Continue an approved guided workflow into live production"},
```

Add help footer lines near existing workflow help:

```python
"/workflow-catalog - Show available workflow capabilities.",
"/workflow-run-guided <path> [fixture] - Run guided workflow phases until approval.",
"/workflow-continue-guided <path> <approval-run-id> [fixture] - Continue approved guided workflow production.",
```

- [ ] **Step 6: Add TUI dispatch**

In the workflow command dispatch block near `_run_workflow_live_plan_command`, insert:

```python
                if self._run_workflow_catalog_command(prompt):
                    continue
                if self._run_workflow_run_guided_command(prompt):
                    continue
                if self._run_workflow_continue_guided_command(prompt):
                    continue
```

- [ ] **Step 7: Add TUI handlers**

Add methods near `_run_workflow_live_plan_command()`:

```python
    def _run_workflow_catalog_command(self, prompt: str) -> bool:
        if prompt.strip() != "/workflow-catalog":
            return False
        rendered = format_workflow_capability_catalog(build_workflow_capability_catalog())
        self.history.add(StatusLine("Workflow capability catalog", kind="info"))
        self.history.add(Text(rendered))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()
        return True

    def _run_workflow_run_guided_command(self, prompt: str) -> bool:
        if not _is_workflow_run_guided_command(prompt):
            return False
        if self._is_turn_active() or self.app.session.is_streaming:
            self._render_workflow_guided_error("wait for the current turn to finish")
            return True
        args = prompt.split()
        if len(args) not in {2, 3}:
            self._render_workflow_guided_error("expected /workflow-run-guided <path> [fixture]")
            return True
        try:
            package_path = _resolve_workflow_run_package_path(self.app.cwd, args[1])
            fixture_name = args[2] if len(args) == 3 else "happy_path.yaml"
            report = prepare_guided_workflow_run(
                WorkflowRunRequest(
                    package_path=package_path,
                    fixture_name=fixture_name,
                    mode=WorkflowRunMode.LIVE_READ,
                    operator_env=workflow_operator_env_from_env(os.environ),
                ),
                live_read_broker=LiveWorkflowBroker(build_live_read_transports_from_env(os.environ), mode="live-read"),
                credential_resolver=credential_resolver_for_package_env(package_path, os.environ),
            )
        except Exception as error:  # noqa: BLE001 - command failures should render, not crash TUI
            self._render_workflow_guided_error(str(error))
            return True
        kind = "info" if report.status in {"passed", "approval-required"} else "error"
        self.history.add(StatusLine(f"Workflow guided run {report.status}", kind=kind))
        self.history.add(Text(format_guided_workflow_run_report(report)))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()
        return True

    def _run_workflow_continue_guided_command(self, prompt: str) -> bool:
        if not _is_workflow_continue_guided_command(prompt):
            return False
        if self._is_turn_active() or self.app.session.is_streaming:
            self._render_workflow_guided_error("wait for the current turn to finish")
            return True
        args = prompt.split()
        if len(args) not in {3, 4}:
            self._render_workflow_guided_error("expected /workflow-continue-guided <path> <approval-run-id> [fixture]")
            return True
        try:
            package_path = _resolve_workflow_run_package_path(self.app.cwd, args[1])
            fixture_name = args[3] if len(args) == 4 else "happy_path.yaml"
            report = continue_guided_workflow_run(
                WorkflowRunRequest(
                    package_path=package_path,
                    fixture_name=fixture_name,
                    mode=WorkflowRunMode.LIVE_PRODUCTION,
                    operator_env=workflow_operator_env_from_env(os.environ),
                ),
                approval_run_id=args[2],
                live_production_broker=LiveWorkflowBroker(
                    build_live_production_transports_from_env(os.environ),
                    mode="live-production",
                    production_enabled=True,
                ),
                credential_resolver=credential_resolver_for_package_env(package_path, os.environ),
                env=os.environ,
            )
        except Exception as error:  # noqa: BLE001 - command failures should render, not crash TUI
            self._render_workflow_guided_error(str(error))
            return True
        kind = "info" if report.status == "passed" else "error"
        self.history.add(StatusLine(f"Workflow guided continue {report.status}", kind=kind))
        self.history.add(Text(format_guided_workflow_run_report(report)))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()
        return True

    def _render_workflow_guided_error(self, message: str) -> None:
        self.history.add(StatusLine(f"Workflow guided run failed: {message}", kind="error"))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()
```

Add predicates near the existing `_is_workflow_*` helpers:

```python
def _is_workflow_run_guided_command(prompt: str) -> bool:
    return prompt == "/workflow-run-guided" or prompt.startswith("/workflow-run-guided ")


def _is_workflow_continue_guided_command(prompt: str) -> bool:
    return prompt == "/workflow-continue-guided" or prompt.startswith("/workflow-continue-guided ")
```

- [ ] **Step 8: Run TUI tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_tui.py::test_tui_workflow_catalog_command_renders_user_catalog \
  appV2.4/tests/test_tui.py::test_tui_workflow_run_guided_command_renders_report \
  appV2.4/tests/test_tui.py::test_tui_workflow_continue_guided_command_renders_report -q
```

Expected:

```text
3 passed
```

- [ ] **Step 9: Commit TUI surface**

Run:

```bash
git add appV2.4/appv24/tui/interactive_mode.py appV2.4/tests/test_tui.py
git commit -m "feat(appv24): expose guided workflow run in tui"
```

Expected:

```text
One yellow-zone TUI commit with focused tests.
```

## Task 6: Documentation And Final Verification

**Files:**
- Modify: `docs/appv24/workflow-run-live-path.md`

- [ ] **Step 1: Document guided user mode**

Append a section to `docs/appv24/workflow-run-live-path.md`:

```markdown
## Guided User Mode

The operator slash-command sequence remains available for debugging, but the
workflow profile also exposes a guided user path:

```text
/workflow-catalog
/workflow-run-guided <package> [fixture]
/workflow-approve <package> <approval-run-id> <approval-request-id>
/workflow-continue-guided <package> <approval-run-id> [fixture]
```

`/workflow-run-guided` renders the catalog-backed live plan, runs the live-read
prefix, resumes local steps, and stops at the approval ledger. It does not
approve or run live-production.

After the user records approval, `/workflow-continue-guided` checks
live-production readiness and then runs approved live-production through the
same connector, approval, idempotency, and target-policy gates as the lower
level command.
```

- [ ] **Step 2: Run targeted workflow tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_capability_catalog.py \
  appV2.4/tests/test_workflow_guided_run.py \
  appV2.4/tests/test_workflow_live_readiness.py \
  appV2.4/tests/test_cli.py::test_cli_workflow_catalog_command_renders_user_catalog \
  appV2.4/tests/test_cli.py::test_cli_workflow_run_guided_command_invokes_prepare \
  appV2.4/tests/test_cli.py::test_cli_workflow_continue_guided_command_invokes_continue \
  appV2.4/tests/test_tui.py::test_tui_workflow_catalog_command_renders_user_catalog \
  appV2.4/tests/test_tui.py::test_tui_workflow_run_guided_command_renders_report \
  appV2.4/tests/test_tui.py::test_tui_workflow_continue_guided_command_renders_report -q
```

Expected:

```text
All selected tests pass.
```

- [ ] **Step 3: Run full appv24 Python tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Expected:

```text
All tests pass.
```

- [ ] **Step 4: Run appv24 CLI package tests**

Run:

```bash
npm --prefix packages/appv24-cli test
```

Expected:

```text
15 passed
```

- [ ] **Step 5: Run packaging dry run**

Run:

```bash
npm --prefix packages/appv24-cli run pack:dry-run
```

Expected:

```text
Dry run completes without npm publish.
```

- [ ] **Step 6: Check whitespace**

Run:

```bash
git diff --check
```

Expected:

```text
No output.
```

- [ ] **Step 7: Commit docs**

Run:

```bash
git add docs/appv24/workflow-run-live-path.md
git commit -m "docs(appv24): document guided workflow run"
```

Expected:

```text
One docs commit.
```

## Final Acceptance

The MVP is complete when:

- `/workflow-catalog` shows user-facing capabilities derived from connector contracts.
- `/workflow-run-guided <package> [fixture]` runs through live-read and local resume to approval.
- The user still explicitly approves the external side effect.
- `/workflow-continue-guided <package> <approval-run-id> [fixture]` runs readiness and approved live-production.
- Existing lower-level operator commands still work.
- No red-zone files are touched.
- Yellow-zone command changes have focused CLI/TUI tests.
- Full Python tests, CLI tests, packaging dry run, and `git diff --check` pass.
