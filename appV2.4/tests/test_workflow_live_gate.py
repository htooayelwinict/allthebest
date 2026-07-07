from __future__ import annotations

from pathlib import Path

from appv24.workflows.execution import WorkflowRunMode
from appv24.workflows.idempotency import build_idempotency_key
from appv24.workflows.ir import (
    ActionCapability,
    ConnectorCapability,
    ConnectorRef,
    StepKind,
    WorkflowDesignContract,
    WorkflowIR,
    WorkflowStep,
    WorkflowTrigger,
)
from appv24.workflows.live_gate import evaluate_live_gate


def test_live_gate_passes_allowlisted_http_fetch_with_allowed_host() -> None:
    decision = evaluate_live_gate(
        _ir(
            [
                _step("fetch_news", "http.fetch", with_={"url": "https://api.example.com/news.json"}, next_step="done"),
                _terminal("done"),
            ],
            [_action("http", "fetch", allowed_outputs=("status_code", "url", "json", "text"), scopes=("http:read",))],
        ),
        {"input": {}},
        mode=WorkflowRunMode.LIVE_READ,
        package_fingerprint="sha256:test",
        connector_allowlist={"http.fetch"},
        approval_index={},
        available_transports={"http.fetch"},
        env={"APPV24_HTTP_FETCH_ALLOWED_HOSTS": "api.example.com"},
    )

    assert decision.ok is True
    assert decision.checked_actions == ("http.fetch",)
    assert decision.issues == ()


def test_live_gate_blocks_uncataloged_action() -> None:
    decision = evaluate_live_gate(
        _ir(
            [
                _step("custom_read", "custom.read", next_step="done"),
                _terminal("done"),
            ],
            [_action("custom", "read")],
        ),
        {"input": {}},
        mode=WorkflowRunMode.LIVE_READ,
        package_fingerprint="sha256:test",
        connector_allowlist={"custom.read"},
        approval_index={},
        available_transports={"custom.read"},
        env={},
    )

    assert decision.ok is False
    assert decision.checked_actions == ("custom.read",)
    assert decision.issues[0].code == "uncataloged_action"
    assert decision.issues[0].message == "live-read action is not cataloged: custom.read"


def test_live_gate_blocks_mutating_action_in_live_read() -> None:
    decision = evaluate_live_gate(
        _ir(
            [
                _step("draft_reply", "gmail.create_draft", side_effect_class="create", approval="human-review", next_step="done"),
                _terminal("done"),
            ],
            [
                _action(
                    "gmail",
                    "create_draft",
                    side_effect=True,
                    side_effect_class="create",
                    approval_required=True,
                    idempotency=("to", "subject"),
                    scopes=("gmail.compose",),
                )
            ],
        ),
        {"input": {}},
        mode=WorkflowRunMode.LIVE_READ,
        package_fingerprint="sha256:test",
        connector_allowlist={"gmail.create_draft"},
        approval_index={},
        available_transports={"gmail.create_draft"},
        env={},
    )

    assert decision.ok is False
    assert decision.checked_actions == ("gmail.create_draft",)
    assert decision.issues[0].code == "mode_mismatch"
    assert decision.issues[0].message == "live-read cannot execute mutating step draft_reply"


def test_live_gate_allows_live_read_prefix_before_unallowlisted_local_action() -> None:
    decision = evaluate_live_gate(
        _ir(
            [
                _step("search_news", "brave.search", with_={"query": "market catalysts"}, next_step="extract_news"),
                _step("extract_news", "llm.extract_structured", with_={"output_contract": "summary_v1"}, next_step="done"),
                _terminal("done"),
            ],
            [
                _action("brave", "search", allowed_outputs=("items", "source_count", "query"), scopes=("brave:search")),
                _action("llm", "extract_structured", allowed_outputs=("summary", "raw_response"), scopes=()),
            ],
        ),
        {"input": {}},
        mode=WorkflowRunMode.LIVE_READ,
        package_fingerprint="sha256:test",
        connector_allowlist={"brave.search"},
        approval_index={},
        available_transports={"brave.search"},
        env={},
    )

    assert decision.ok is True
    assert decision.checked_actions == ("brave.search",)
    assert decision.issues == ()


def test_live_gate_blocks_missing_configured_transport() -> None:
    decision = evaluate_live_gate(
        _ir(
            [
                _step("inspect_alerts", "github.list_dependabot_alerts", next_step="done"),
                _terminal("done"),
            ],
            [_action("github", "list_dependabot_alerts", allowed_outputs=("alerts",), scopes=("dependabot:read",))],
        ),
        {"input": {}},
        mode=WorkflowRunMode.LIVE_READ,
        package_fingerprint="sha256:test",
        connector_allowlist={"github.list_dependabot_alerts"},
        approval_index={},
        available_transports=set(),
        env={},
    )

    assert decision.ok is False
    assert decision.issues[0].code == "missing_transport"
    assert decision.issues[0].message.startswith("live-read transport is not configured for github.list_dependabot_alerts")


def test_live_gate_blocks_http_fetch_host_outside_configured_allowlist() -> None:
    decision = evaluate_live_gate(
        _ir(
            [
                _step("fetch_news", "http.fetch", with_={"url": "https://evil.example/news.json"}, next_step="done"),
                _terminal("done"),
            ],
            [_action("http", "fetch", allowed_outputs=("status_code", "url", "json", "text"), scopes=("http:read",))],
        ),
        {"input": {}},
        mode=WorkflowRunMode.LIVE_READ,
        package_fingerprint="sha256:test",
        connector_allowlist={"http.fetch"},
        approval_index={},
        available_transports={"http.fetch"},
        env={"APPV24_HTTP_FETCH_ALLOWED_HOSTS": "api.example.com"},
    )

    assert decision.ok is False
    assert decision.issues[0].code == "target_not_allowlisted"
    assert decision.issues[0].message == "http.fetch URL host is not allowlisted: evil.example"


def test_live_gate_blocks_telegram_target_outside_configured_allowlist() -> None:
    idempotency_key = build_idempotency_key(
        package_fingerprint="sha256:test",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        key_fields=("chat_id", "text"),
        input_ref={"with": {"chat_id": {"inputRef": "chat_id"}, "text": {"inputRef": "text"}}},
        fixture={"input": {"chat_id": "customer-chat", "text": "Ticket created."}},
    )

    decision = evaluate_live_gate(
        _ir(
            [
                _step(
                    "notify_admin",
                    "telegram.send_message",
                    with_={"chat_id": {"inputRef": "chat_id"}, "text": {"inputRef": "text"}},
                    side_effect_class="send",
                    approval="admin-review",
                    next_step="done",
                ),
                _terminal("done"),
            ],
            [
                _action(
                    "telegram",
                    "send_message",
                    side_effect=True,
                    side_effect_class="send",
                    approval_required=True,
                    idempotency=("chat_id", "text"),
                    scopes=("telegram:send",),
                    allowed_outputs=("message_id", "chat_id"),
                )
            ],
        ),
        {"input": {"chat_id": "customer-chat", "text": "Ticket created."}},
        mode=WorkflowRunMode.LIVE_PRODUCTION,
        package_fingerprint="sha256:test",
        connector_allowlist={"telegram.send_message"},
        approval_index={("notify_admin", "telegram.send_message", idempotency_key): True},
        available_transports={"telegram.send_message"},
        env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat"},
    )

    assert decision.ok is False
    assert decision.issues[0].code == "target_not_allowlisted"
    assert decision.issues[0].message == "telegram.send_message chat_id is not allowlisted: customer-chat"


def test_live_gate_blocks_telegram_when_allowed_chat_ids_missing() -> None:
    fixture = {"input": {"chat_id": "admin-chat", "text": "Ticket created."}}
    step_raw = {"with": {"chat_id": {"inputRef": "chat_id"}, "text": {"inputRef": "text"}}}
    idempotency_key = build_idempotency_key(
        package_fingerprint="sha256:test",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        key_fields=("chat_id", "text"),
        input_ref=step_raw,
        fixture=fixture,
    )

    decision = evaluate_live_gate(
        _ir(
            [
                _step(
                    "notify_admin",
                    "telegram.send_message",
                    with_=step_raw["with"],
                    side_effect_class="send",
                    approval="admin-review",
                    next_step="done",
                ),
                _terminal("done"),
            ],
            [
                _action(
                    "telegram",
                    "send_message",
                    side_effect=True,
                    side_effect_class="send",
                    approval_required=True,
                    idempotency=("chat_id", "text"),
                    scopes=("telegram:send",),
                    allowed_outputs=("message_id", "chat_id"),
                )
            ],
        ),
        fixture,
        mode=WorkflowRunMode.LIVE_PRODUCTION,
        package_fingerprint="sha256:test",
        connector_allowlist={"telegram.send_message"},
        approval_index={("notify_admin", "telegram.send_message", idempotency_key): True},
        available_transports={"telegram.send_message"},
        env={},
    )

    assert decision.ok is False
    assert decision.issues[0].code == "missing_target_allowlist"
    assert decision.issues[0].message == "telegram.send_message requires APPV24_TELEGRAM_ALLOWED_CHAT_IDS"


def test_live_gate_requires_idempotency_key_fields_for_mutating_action() -> None:
    decision = evaluate_live_gate(
        _ir(
            [
                _step(
                    "notify_admin",
                    "telegram.send_message",
                    with_={"chat_id": "admin-chat", "text": "Ticket created."},
                    side_effect_class="send",
                    approval="admin-review",
                    next_step="done",
                ),
                _terminal("done"),
            ],
            [
                _action(
                    "telegram",
                    "send_message",
                    side_effect=True,
                    side_effect_class="send",
                    approval_required=True,
                    idempotency=(),
                    scopes=("telegram:send",),
                    allowed_outputs=("message_id", "chat_id"),
                )
            ],
        ),
        {"input": {}},
        mode=WorkflowRunMode.LIVE_PRODUCTION,
        package_fingerprint="sha256:test",
        connector_allowlist={"telegram.send_message"},
        approval_index={},
        available_transports={"telegram.send_message"},
        env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat"},
    )

    assert decision.ok is False
    assert decision.issues[0].code == "missing_idempotency"
    assert decision.issues[0].message == "live-production requires idempotency key fields for notify_admin"


def test_live_gate_passes_approved_production_send() -> None:
    fixture = {"input": {"chat_id": "admin-chat", "text": "Ticket created."}}
    step_raw = {"with": {"chat_id": {"inputRef": "chat_id"}, "text": {"inputRef": "text"}}}
    idempotency_key = build_idempotency_key(
        package_fingerprint="sha256:test",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        key_fields=("chat_id", "text"),
        input_ref=step_raw,
        fixture=fixture,
    )

    decision = evaluate_live_gate(
        _ir(
            [
                _step(
                    "notify_admin",
                    "telegram.send_message",
                    with_=step_raw["with"],
                    side_effect_class="send",
                    approval="admin-review",
                    next_step="done",
                ),
                _terminal("done"),
            ],
            [
                _action(
                    "telegram",
                    "send_message",
                    side_effect=True,
                    side_effect_class="send",
                    approval_required=True,
                    idempotency=("chat_id", "text"),
                    scopes=("telegram:send",),
                    allowed_outputs=("message_id", "chat_id"),
                )
            ],
        ),
        fixture,
        mode=WorkflowRunMode.LIVE_PRODUCTION,
        package_fingerprint="sha256:test",
        connector_allowlist={"telegram.send_message"},
        approval_index={("notify_admin", "telegram.send_message", idempotency_key): True},
        available_transports={"telegram.send_message"},
        env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat"},
    )

    assert decision.ok is True
    assert decision.checked_actions == ("telegram.send_message",)
    assert decision.issues == ()


def _ir(steps: list[WorkflowStep], actions: list[ActionCapability]) -> WorkflowIR:
    connectors: dict[str, ConnectorCapability] = {}
    for action in actions:
        connector = connectors.get(action.connector_id)
        action_map = dict(connector.actions) if connector is not None else {}
        action_map[action.action_id] = action
        connectors[action.connector_id] = ConnectorCapability(
            connector_id=action.connector_id,
            adapter=action.adapter,
            actions=action_map,
            raw={},
        )
    return WorkflowIR(
        root=Path("."),
        name="test-workflow",
        version="appv24.workflow/v1",
        entrypoint=steps[0].step_id,
        triggers=(WorkflowTrigger(trigger_id="manual", trigger_type="manual", raw={}),),
        steps={step.step_id: step for step in steps},
        connectors=connectors,
        approvals={},
        design=WorkflowDesignContract(
            intent="test",
            trigger="manual",
            inputs=(),
            outputs=(),
            assumptions=(),
            side_effects=(),
            approval_gates=(),
            failure_modes=(),
            rollback=(),
        ),
    )


def _step(
    step_id: str,
    action_ref: str,
    *,
    with_: dict[str, object] | None = None,
    side_effect_class: str | None = None,
    approval: str | None = None,
    next_step: str | None = None,
) -> WorkflowStep:
    connector_id, action_id = action_ref.split(".", 1)
    raw: dict[str, object] = {"uses": action_ref}
    if with_:
        raw["with"] = with_
    return WorkflowStep(
        step_id=step_id,
        kind=StepKind.ACTION,
        uses=ConnectorRef(connector_id, action_id),
        side_effect_class=side_effect_class,
        approval=approval,
        next_step=next_step,
        branches={},
        outbound=tuple(filter(None, (next_step,))),
        raw=raw,
    )


def _terminal(step_id: str) -> WorkflowStep:
    return WorkflowStep(
        step_id=step_id,
        kind=StepKind.TERMINAL,
        uses=None,
        side_effect_class=None,
        approval=None,
        next_step=None,
        branches={},
        outbound=(),
        raw={"type": "terminal"},
    )


def _action(
    connector_id: str,
    action_id: str,
    *,
    side_effect: bool = False,
    side_effect_class: str | None = None,
    approval_required: bool | None = None,
    idempotency: tuple[str, ...] = (),
    allowed_outputs: tuple[str, ...] = ("ok",),
    scopes: tuple[str, ...] = (),
) -> ActionCapability:
    return ActionCapability(
        connector_id=connector_id,
        action_id=action_id,
        adapter="direct_api",
        side_effect=side_effect,
        side_effect_class=side_effect_class,
        execution_mode="brokered",
        approval_required=approval_required,
        dry_run_supported=True,
        idempotency_key_fields=idempotency,
        allowed_outputs=allowed_outputs,
        required_scopes=scopes,
        granted_scopes=scopes,
        raw={},
    )
