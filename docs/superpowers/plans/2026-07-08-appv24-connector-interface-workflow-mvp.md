# appv24 Connector Interface Workflow MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the connector-interface MVP so a TUI user can generate an AI-news Telegram workflow that uses cataloged connector actions, runs live-read, requests approval on the Telegram production connector step, and runs approved live-production through connector adapters.

**Architecture:** Keep workflow as orchestration only. Add `telegram.list_subscribers` and `telegram.broadcast_message` as connector-owned interfaces in the contract catalog, local providers, live transports, live gate, and live plan. Workflow generation must depend on catalog actions, not workflow-local subscriber or formatting behavior. For the MVP golden path, place live-read connector actions before local summarization: `brave.search -> telegram.list_subscribers -> llm.summarize -> telegram.broadcast_message`.

**Tech Stack:** Python 3.13, pytest, appv24 workflow modules, existing `BrokerRequest`/`BrokerResult`, `LiveWorkflowTransport`, TUI slash-command handlers, YAML workflow packages.

---

## File Structure

- Modify `appV2.4/appv24/workflows/connector_contracts.py`
  - Add contract entries for `telegram.list_subscribers` and `telegram.broadcast_message`.
  - Keep target policy and secret refs in connector contract layer.
- Modify `appV2.4/appv24/workflows/prompt_fragments.py`
  - Make catalog prompt steer registered-user requests to Telegram connector interfaces.
- Modify `appV2.4/appv24/workflows/local_artifacts.py`
  - Add local providers for subscriber listing and broadcast artifacts.
- Modify `appV2.4/appv24/workflows/live_transports.py`
  - Add live-read subscriber adapter and live-production broadcast adapter.
  - Register them in env-gated live transport builders.
- Modify `appV2.4/appv24/workflows/live_gate.py`
  - Enforce target policy for broadcast recipient lists.
  - Resolve prior-step output templates when checking targets.
- Modify `appV2.4/appv24/workflows/live_plan.py`
  - Include new Telegram actions in built-in live-read and live-production transport sets.
- Modify focused tests under `appV2.4/tests/`
  - Contract, prompt, local artifact, live transport, live gate/readiness, phase runner, and TUI/CLI command output coverage.

No kernel/red-zone files are part of this plan.

## Task 1: Add Telegram Connector Contracts

**Files:**
- Modify: `appV2.4/tests/test_workflow_connector_contracts.py`
- Modify: `appV2.4/appv24/workflows/connector_contracts.py`
- Modify: `appV2.4/appv24/workflows/prompt_fragments.py`

- [ ] **Step 1: Write the failing contract tests**

Add these tests to `appV2.4/tests/test_workflow_connector_contracts.py`:

```python
def test_telegram_subscriber_and_broadcast_contracts_are_connector_interfaces() -> None:
    catalog = builtin_connector_contract_catalog()

    subscribers = catalog["telegram.list_subscribers"]
    assert subscribers.approval_class is ApprovalClass.NONE
    assert subscribers.side_effect_class is SideEffectClass.READ
    assert subscribers.required_scopes == ("telegram:subscribers:read",)
    assert subscribers.allowed_outputs == ("recipients", "recipient_count", "audience_hash", "artifact_path")
    assert subscribers.secrets[0].env_name == "APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
    assert WorkflowLiveMode.LIVE_READ in subscribers.live_modes
    assert subscribers.prompt_summary == (
        "telegram.list_subscribers(input: audience, limit) -> recipients, recipient_count, audience_hash"
    )

    broadcast = catalog["telegram.broadcast_message"]
    assert broadcast.approval_class is ApprovalClass.EXTERNAL_SEND
    assert broadcast.side_effect_class is SideEffectClass.SEND
    assert broadcast.required_scopes == ("telegram:send",)
    assert broadcast.idempotency_key_fields == ("audience_hash", "text")
    assert broadcast.target_policy.allowed_chat_ids_env == "APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
    assert broadcast.allowed_outputs == ("sent_count", "message_ids", "chat_ids", "artifact_path")
    assert WorkflowLiveMode.LIVE_PRODUCTION in broadcast.live_modes
    assert broadcast.prompt_summary == (
        "telegram.broadcast_message(input: recipients, text) -> sent_count, message_ids, chat_ids"
    )
```

Update `test_workflow_operator_env_uses_only_target_policy_env_names` in the same file so it still expects only target-policy names:

```python
assert workflow_operator_env_names() == (
    "APPV24_HTTP_FETCH_ALLOWED_HOSTS",
    "APPV24_TELEGRAM_ALLOWED_CHAT_IDS",
)
```

Update `test_capability_prompt_uses_slim_contract_summaries`:

```python
assert "telegram.list_subscribers(input: audience, limit) -> recipients, recipient_count, audience_hash" in fragment
assert "telegram.broadcast_message(input: recipients, text) -> sent_count, message_ids, chat_ids" in fragment
assert "For Telegram registered-user workflows, use telegram.list_subscribers before telegram.broadcast_message." in fragment
assert "fetch_user_list" not in fragment
assert "format_news_digest" not in fragment
```

- [ ] **Step 2: Run the contract tests to verify red**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_connector_contracts.py -q
```

Expected: failure because `telegram.list_subscribers` and `telegram.broadcast_message` are not in `builtin_connector_contract_catalog()`.

- [ ] **Step 3: Add the contracts**

In `appV2.4/appv24/workflows/connector_contracts.py`, insert these `_contract(...)` entries before the existing `telegram.send_message` contract:

```python
        _contract(
            "telegram.list_subscribers",
            input_fields=("audience", "limit"),
            allowed_outputs=("recipients", "recipient_count", "audience_hash", "artifact_path"),
            required_scopes=("telegram:subscribers:read",),
            secrets=(ConnectorSecretRef("APPV24_TELEGRAM_ALLOWED_CHAT_IDS", "Telegram subscriber allowlist"),),
            live_modes=(WorkflowLiveMode.LIVE_READ,),
            retry_class=RetryClass.READ_SAFE,
            target_policy=ConnectorTargetPolicy(allowed_chat_ids_env="APPV24_TELEGRAM_ALLOWED_CHAT_IDS"),
            prompt_summary=(
                "telegram.list_subscribers(input: audience, limit) -> "
                "recipients, recipient_count, audience_hash"
            ),
        ),
        _contract(
            "telegram.broadcast_message",
            input_fields=(
                "recipients",
                "audience_hash",
                "text",
                "parse_mode",
                "disable_notification",
                "protect_content",
            ),
            allowed_outputs=("sent_count", "message_ids", "chat_ids", "artifact_path"),
            required_scopes=("telegram:send",),
            secrets=(ConnectorSecretRef("APPV24_TELEGRAM_BOT_TOKEN", "Telegram bot token"),),
            side_effect_class=SideEffectClass.SEND,
            approval_class=ApprovalClass.EXTERNAL_SEND,
            live_modes=(WorkflowLiveMode.LIVE_PRODUCTION,),
            idempotency_key_fields=("audience_hash", "text"),
            retry_class=RetryClass.IDEMPOTENT_WRITE,
            target_policy=ConnectorTargetPolicy(allowed_chat_ids_env="APPV24_TELEGRAM_ALLOWED_CHAT_IDS"),
            prompt_summary="telegram.broadcast_message(input: recipients, text) -> sent_count, message_ids, chat_ids",
        ),
```

In `appV2.4/appv24/workflows/prompt_fragments.py`, replace the existing subscriber/broadcast warning line with:

```python
        "- For Telegram registered-user workflows, use telegram.list_subscribers before telegram.broadcast_message.",
        "- Do not invent fetch_user_list, get_registered_users, format_news_digest, send_broadcast, or local subscriber actions.",
```

- [ ] **Step 4: Run contract tests to verify green**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_connector_contracts.py -q
```

Expected: all tests in `test_workflow_connector_contracts.py` pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add appV2.4/tests/test_workflow_connector_contracts.py appV2.4/appv24/workflows/connector_contracts.py appV2.4/appv24/workflows/prompt_fragments.py
git commit -m "feat(appv24): add telegram subscriber connector contracts"
```

## Task 2: Add Local Artifact Providers For Telegram Interfaces

**Files:**
- Modify: `appV2.4/tests/test_workflow_local_artifacts.py`
- Modify: `appV2.4/appv24/workflows/local_artifacts.py`

- [ ] **Step 1: Write failing local provider tests**

Add these tests to `appV2.4/tests/test_workflow_local_artifacts.py`:

```python
def test_telegram_list_subscribers_provider_uses_fixture_or_allowed_outputs(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)

    result = providers.telegram_list_subscribers(
        BrokerRequest(
            step_id="list_subscribers",
            connector_action="telegram.list_subscribers",
            input_ref={"with": {"audience": "default"}},
            fixture={"input": {"recipients": ["admin-chat", "ops-chat"]}},
            allowed_outputs=("recipients", "recipient_count", "audience_hash", "artifact_path"),
        )
    )

    assert result.status == "simulated"
    assert result.outputs["recipients"] == ["admin-chat", "ops-chat"]
    assert result.outputs["recipient_count"] == 2
    assert str(result.outputs["audience_hash"]).startswith("audience:")
    assert Path(str(result.outputs["artifact_path"])).is_relative_to(tmp_path)


def test_telegram_broadcast_message_provider_writes_local_broadcast_artifact(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)

    result = providers.telegram_broadcast_message(
        BrokerRequest(
            step_id="send_digest",
            connector_action="telegram.broadcast_message",
            input_ref={"with": {"text": "Daily AI digest"}},
            fixture={"input": {"recipients": ["admin-chat", "ops-chat"]}},
            allowed_outputs=("sent_count", "message_ids", "chat_ids", "artifact_path"),
        )
    )

    assert result.status == "simulated"
    assert result.outputs["sent_count"] == 2
    assert result.outputs["chat_ids"] == ["admin-chat", "ops-chat"]
    assert result.outputs["message_ids"] == ["local-message-send_digest-1", "local-message-send_digest-2"]
    assert Path(str(result.outputs["artifact_path"])).is_relative_to(tmp_path)
```

Update `test_local_artifact_provider_map_exposes_supported_actions` expected action list by adding:

```python
        "telegram.broadcast_message",
        "telegram.list_subscribers",
```

- [ ] **Step 2: Run local artifact tests to verify red**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_local_artifacts.py -q
```

Expected: failure because `LocalArtifactProviders` does not expose the new provider methods.

- [ ] **Step 3: Implement local providers**

In `appV2.4/appv24/workflows/local_artifacts.py`, add imports:

```python
import hashlib
```

Add helper functions near the bottom:

```python
def _fixture_recipients(request: BrokerRequest) -> list[str]:
    fixture_input = request.fixture.get("input") if isinstance(request.fixture, dict) else None
    if isinstance(fixture_input, dict):
        recipients = fixture_input.get("recipients") or fixture_input.get("chat_ids")
        if isinstance(recipients, list):
            return [str(item).strip() for item in recipients if str(item).strip()]
    return ["local-telegram-subscriber"]


def _audience_hash(recipients: list[str]) -> str:
    encoded = json.dumps(sorted(recipients), sort_keys=True, separators=(",", ":"))
    return f"audience:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"
```

Add methods to `LocalArtifactProviders`:

```python
    def telegram_list_subscribers(self, request: BrokerRequest) -> BrokerResult:
        recipients = _fixture_recipients(request)
        path = self._write_artifact(request, "local-telegram-subscribers")
        return _filtered(
            request,
            {
                "recipients": recipients,
                "recipient_count": len(recipients),
                "audience_hash": _audience_hash(recipients),
                "artifact_path": str(path),
            },
        )

    def telegram_broadcast_message(self, request: BrokerRequest) -> BrokerResult:
        recipients = _fixture_recipients(request)
        path = self._write_artifact(request, "local-telegram-broadcast")
        return _filtered(
            request,
            {
                "sent_count": len(recipients),
                "message_ids": [f"local-message-{request.step_id}-{index}" for index, _ in enumerate(recipients, start=1)],
                "chat_ids": recipients,
                "artifact_path": str(path),
            },
        )
```

Add both actions to `provider_map()`:

```python
            "telegram.broadcast_message": self.telegram_broadcast_message,
            "telegram.list_subscribers": self.telegram_list_subscribers,
```

- [ ] **Step 4: Run local artifact tests to verify green**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_local_artifacts.py -q
```

Expected: all tests in `test_workflow_local_artifacts.py` pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add appV2.4/tests/test_workflow_local_artifacts.py appV2.4/appv24/workflows/local_artifacts.py
git commit -m "feat(appv24): simulate telegram subscriber connectors"
```

## Task 3: Add Live Telegram Subscriber And Broadcast Transports

**Files:**
- Modify: `appV2.4/tests/test_workflow_live_transports.py`
- Modify: `appV2.4/appv24/workflows/live_transports.py`

- [ ] **Step 1: Write failing live transport tests**

Add tests near the existing Telegram transport tests in `appV2.4/tests/test_workflow_live_transports.py`:

```python
def test_live_read_transport_registry_registers_telegram_list_subscribers() -> None:
    transports = build_live_read_transports_from_env(
        {
            "APPV24_WORKFLOW_LIVE_READ_ENABLED": "true",
            "APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat,ops-chat",
        }
    )

    assert "telegram.list_subscribers" in transports


def test_telegram_list_subscribers_transport_returns_allowed_recipients() -> None:
    transport = TelegramListSubscribersTransport(allowed_chat_ids=("admin-chat", "ops-chat"))

    result = transport.invoke(
        BrokerRequest(
            step_id="list_subscribers",
            connector_action="telegram.list_subscribers",
            input_ref={"with": {"audience": "default"}},
            fixture={"input": {}},
            allowed_outputs=("recipients", "recipient_count", "audience_hash"),
            dry_run=False,
        )
    )

    assert result.status == "live-read"
    assert result.provider_id == "telegram"
    assert result.outputs["recipients"] == ["admin-chat", "ops-chat"]
    assert result.outputs["recipient_count"] == 2
    assert str(result.outputs["audience_hash"]).startswith("audience:")


def test_telegram_broadcast_message_transport_posts_to_each_allowed_recipient() -> None:
    client = FakeClient(
        {"ok": True, "result": {"message_id": 42, "chat": {"id": "admin-chat"}}},
        post_responses=[
            FakeResponse({"ok": True, "result": {"message_id": 42, "chat": {"id": "admin-chat"}}}),
            FakeResponse({"ok": True, "result": {"message_id": 43, "chat": {"id": "ops-chat"}}}),
        ],
    )
    transport = TelegramBroadcastMessageTransport(
        token="telegram-token",
        base_url="https://telegram.test",
        allowed_chat_ids=("admin-chat", "ops-chat"),
        client_factory=lambda: client,
    )

    result = transport.invoke(
        BrokerRequest(
            step_id="send_digest",
            connector_action="telegram.broadcast_message",
            input_ref={
                "with": {
                    "recipients": "{{ steps.list_subscribers.outputs.recipients }}",
                    "text": "{{ steps.summarize_digest.outputs.summary }}",
                    "disable_notification": True,
                }
            },
            fixture={
                "steps": {
                    "list_subscribers": {"outputs": {"recipients": ["admin-chat", "ops-chat"]}},
                    "summarize_digest": {"outputs": {"summary": "Daily AI digest"}},
                }
            },
            allowed_outputs=("sent_count", "message_ids", "chat_ids"),
            dry_run=False,
        )
    )

    assert result.status == "live-production"
    assert result.outputs == {
        "sent_count": 2,
        "message_ids": ["42", "43"],
        "chat_ids": ["admin-chat", "ops-chat"],
    }
    assert [call["json"]["chat_id"] for call in client.post_calls] == ["admin-chat", "ops-chat"]
    assert all(call["json"]["text"] == "Daily AI digest" for call in client.post_calls)
    assert "telegram-token" not in repr(result.outputs)


def test_telegram_broadcast_message_transport_blocks_unapproved_recipient_before_http() -> None:
    client = FakeClient({"ok": True})
    transport = TelegramBroadcastMessageTransport(
        token="telegram-token",
        allowed_chat_ids=("admin-chat",),
        client_factory=lambda: client,
    )

    result = transport.invoke(
        BrokerRequest(
            step_id="send_digest",
            connector_action="telegram.broadcast_message",
            input_ref={"with": {"recipients": ["admin-chat", "other-chat"], "text": "Daily AI digest"}},
            fixture={"input": {}},
            allowed_outputs=("sent_count", "message_ids", "chat_ids"),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("telegram.broadcast_message chat_id is not allowlisted: other-chat",)
    assert client.post_calls == []
```

- [ ] **Step 2: Run live transport tests to verify red**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_transports.py -q
```

Expected: import or assertion failures because the new transport classes and registry entries do not exist.

- [ ] **Step 3: Implement transport registry entries**

In `build_live_read_transports_from_env`, add:

```python
    telegram_allowed_chat_ids = _split_csv(env.get(TELEGRAM_ALLOWED_CHAT_IDS_ENV, ""))
    if telegram_allowed_chat_ids:
        transports["telegram.list_subscribers"] = TelegramListSubscribersTransport(
            allowed_chat_ids=telegram_allowed_chat_ids,
        )
```

In `build_live_production_transports_from_env`, return both Telegram production transports:

```python
    return {
        "telegram.send_message": TelegramSendMessageTransport(
            token=telegram_token,
            base_url=base_url,
            allowed_chat_ids=allowed_chat_ids,
            client_factory=client_factory,
        ),
        "telegram.broadcast_message": TelegramBroadcastMessageTransport(
            token=telegram_token,
            base_url=base_url,
            allowed_chat_ids=allowed_chat_ids,
            client_factory=client_factory,
        ),
    }
```

- [ ] **Step 4: Implement transport classes and helpers**

In `appV2.4/appv24/workflows/live_transports.py`, add `hashlib` import:

```python
import hashlib
```

Add `TelegramListSubscribersTransport` before `TelegramSendMessageTransport`:

```python
class TelegramListSubscribersTransport:
    """Read-only Telegram subscriber interface backed by configured allowed chat IDs."""

    def __init__(self, *, allowed_chat_ids: tuple[str, ...]) -> None:
        self._allowed_chat_ids = tuple(chat_id for chat_id in allowed_chat_ids if chat_id)

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        if not self._allowed_chat_ids:
            return _blocked(f"telegram.list_subscribers requires {TELEGRAM_ALLOWED_CHAT_IDS_ENV}", provider_id="telegram")
        recipients = list(self._allowed_chat_ids)
        return BrokerResult(
            status="live-read",
            provider_id="telegram",
            outputs={
                "recipients": recipients,
                "recipient_count": len(recipients),
                "audience_hash": _telegram_audience_hash(recipients),
            },
        )
```

Add `TelegramBroadcastMessageTransport` after `TelegramSendMessageTransport`:

```python
class TelegramBroadcastMessageTransport:
    """Production Telegram broadcast transport for an approved connector-owned recipient list."""

    def __init__(
        self,
        *,
        token: str,
        base_url: str = DEFAULT_TELEGRAM_API_BASE_URL,
        allowed_chat_ids: tuple[str, ...] = (),
        client_factory: Callable[[], _HttpClient] | None = None,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._allowed_chat_ids = tuple(allowed_chat_ids)
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=15.0))

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        fields = _telegram_broadcast_fields_for_request(request)
        if fields is None:
            return _blocked("telegram.broadcast_message requires recipients and text", provider_id="telegram")
        recipients = fields["recipients"]
        if not self._allowed_chat_ids:
            return _blocked(f"telegram.broadcast_message requires {TELEGRAM_ALLOWED_CHAT_IDS_ENV}", provider_id="telegram")
        for chat_id in recipients:
            if chat_id not in self._allowed_chat_ids:
                return _blocked(f"telegram.broadcast_message chat_id is not allowlisted: {chat_id}", provider_id="telegram")
        if len(fields["text"]) > TELEGRAM_MAX_MESSAGE_CHARS:
            return _blocked(f"telegram.broadcast_message text exceeds {TELEGRAM_MAX_MESSAGE_CHARS} characters", provider_id="telegram")

        client = self._client_factory()
        message_ids: list[str] = []
        chat_ids: list[str] = []
        try:
            for chat_id in recipients:
                payload = _telegram_send_message_payload({**fields, "chat_id": chat_id})
                response = client.post(
                    f"{self._base_url}/bot{self._token}/sendMessage",
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                parsed = response.json()
                slim = _telegram_send_message_outputs(parsed)
                if slim is None:
                    return _blocked("telegram.broadcast_message returned malformed response", provider_id="telegram")
                message_ids.append(str(slim["message_id"]))
                chat_ids.append(str(slim["chat_id"]))
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code if error.response is not None else "unknown"
            return _blocked(f"telegram.broadcast_message returned HTTP {status_code}", provider_id="telegram")
        except httpx.RequestError as error:
            return _blocked(f"telegram.broadcast_message request failed: {type(error).__name__}", provider_id="telegram")
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        return BrokerResult(
            status="live-production",
            provider_id="telegram",
            outputs={"sent_count": len(message_ids), "message_ids": message_ids, "chat_ids": chat_ids},
        )
```

Add helpers near the existing Telegram helper functions:

```python
def _telegram_audience_hash(recipients: list[str]) -> str:
    encoded = json.dumps(sorted(recipients), sort_keys=True, separators=(",", ":"))
    return f"audience:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _telegram_broadcast_fields_for_request(request: BrokerRequest) -> dict[str, Any] | None:
    with_map = _mapping(request.input_ref.get("with"))
    fixture_input = _mapping(request.fixture.get("input"))
    recipients = _first_string_list(with_map, fixture_input, "recipients", fixture=request.fixture)
    text = _first_string(with_map, fixture_input, ("text", "message_text", "body"), fixture=request.fixture)
    if not recipients or text is None:
        return None
    fields: dict[str, Any] = {"recipients": recipients, "text": text}
    parse_mode = _first_string(with_map, fixture_input, ("parse_mode",), fixture=request.fixture)
    if parse_mode is not None:
        fields["parse_mode"] = parse_mode
    for key in ("disable_notification", "protect_content"):
        value = _first_bool(with_map, fixture_input, key, fixture=request.fixture)
        if value is not None:
            fields[key] = value
    return fields


def _first_string_list(
    primary: Mapping[str, Any],
    fallback: Mapping[str, Any],
    key: str,
    *,
    fixture: Mapping[str, Any] | None = None,
) -> list[str] | None:
    for source, fixture_input in ((primary, fallback), (fallback, fallback)):
        value = _resolve_input_ref(source.get(key), fixture_input, fixture=fixture)
        if isinstance(value, list):
            values = [_scalar_string(item) for item in value]
            recipients = [item for item in values if item is not None]
            if recipients:
                return recipients
    return None
```

- [ ] **Step 5: Run live transport tests to verify green**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_transports.py -q
```

Expected: all tests in `test_workflow_live_transports.py` pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add appV2.4/tests/test_workflow_live_transports.py appV2.4/appv24/workflows/live_transports.py
git commit -m "feat(appv24): add telegram subscriber live transports"
```

## Task 4: Enforce Broadcast Target Policy In The Live Gate

**Files:**
- Modify: `appV2.4/tests/test_workflow_live_runner.py`
- Modify: `appV2.4/appv24/workflows/live_gate.py`

- [ ] **Step 1: Write failing live gate tests**

Add a focused test to `appV2.4/tests/test_workflow_live_runner.py` near existing Telegram target-policy tests:

```python
def test_live_production_gate_blocks_broadcast_recipient_outside_policy(tmp_path: Path) -> None:
    package = _write_telegram_broadcast_package(tmp_path)
    run_root = tmp_path / ".workflow-runs"
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=False,
        )
    )
    _approve_first_request(run_root, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)

    transport = RecordingTransport({"sent_count": 1, "message_ids": ["1"], "chat_ids": ["admin-chat"]}, status="live-production")
    report = run_live_production_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=run_root,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("telegram.broadcast_message",),
            operator_env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat"},
        ),
        broker=LiveWorkflowBroker({"telegram.broadcast_message": transport}, mode="live-production", production_enabled=True),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
    )

    assert report.status == "blocked"
    assert report.issues == ("telegram.broadcast_message chat_id is not allowlisted: other-chat",)
    assert transport.requests == []
```

Add helper `_write_telegram_broadcast_package` in the same file:

```python
def _write_telegram_broadcast_package(root: Path) -> Path:
    package = root / "telegram-broadcast"
    package.mkdir(parents=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "telegram-broadcast",
                "description": "Send an approved Telegram broadcast.",
                "design": _design(side_effects=["Send Telegram broadcast"], approval_gates=["approve_broadcast"]),
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "list_subscribers",
                "steps": [
                    {"id": "list_subscribers", "uses": "telegram.list_subscribers", "next": "send_digest"},
                    {
                        "id": "send_digest",
                        "uses": "telegram.broadcast_message",
                        "side_effect_class": "send",
                        "approval": "approve_broadcast",
                        "with": {
                            "recipients": "{{ steps.list_subscribers.outputs.recipients }}",
                            "audience_hash": "{{ steps.list_subscribers.outputs.audience_hash }}",
                            "text": {"inputRef": "digest_text"},
                        },
                        "next": "done",
                    },
                    {"id": "done", "type": "terminal"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.connectors/v1",
                "connectors": [
                    {
                        "id": "telegram",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "list_subscribers",
                                "side_effect": False,
                                "required_scopes": ["telegram:subscribers:read"],
                                "outputs": {"allowed": ["recipients", "recipient_count", "audience_hash"]},
                            },
                            {
                                "id": "broadcast_message",
                                "side_effect": True,
                                "side_effect_class": "send",
                                "execution_mode": "brokered",
                                "approval_required": True,
                                "dry_run_supported": True,
                                "required_scopes": ["telegram:send"],
                                "idempotency": {"key_fields": ["audience_hash", "text"]},
                                "outputs": {"allowed": ["sent_count", "message_ids", "chat_ids"]},
                            },
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.permissions/v1",
                "permissions": [
                    {
                        "connector": "telegram",
                        "actions": ["list_subscribers", "broadcast_message"],
                        "scopes": ["telegram:subscribers:read", "telegram:send"],
                    }
                ],
                "approvals": [{"id": "approve_broadcast", "mode": "explicit", "required_for": ["send_digest"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    fixture = {
        "input": {"digest_text": "Daily AI digest"},
        "steps": {
            "list_subscribers": {
                "outputs": {
                    "recipients": ["admin-chat", "other-chat"],
                    "audience_hash": "audience:test",
                }
            }
        },
    }
    (fixtures / "happy_path.yaml").write_text(yaml.safe_dump(fixture, sort_keys=False), encoding="utf-8")
    (fixtures / "rejection_path.yaml").write_text(yaml.safe_dump(fixture, sort_keys=False), encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nReview the Telegram broadcast manually.\n", encoding="utf-8")
    return package
```

- [ ] **Step 2: Run the focused live runner test to verify red**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_runner.py::test_live_production_gate_blocks_broadcast_recipient_outside_policy -q
```

Expected: failure because `live_gate.py` does not resolve `telegram.broadcast_message` recipients from prior step outputs.

- [ ] **Step 3: Implement broadcast target-policy resolution**

In `appV2.4/appv24/workflows/live_gate.py`, change the Telegram policy block:

```python
    if action_ref == "telegram.send_message":
```

to:

```python
    if action_ref in {"telegram.send_message", "telegram.broadcast_message"}:
```

Inside that block, branch by action:

```python
        if action_ref == "telegram.broadcast_message":
            recipients = _field_values(step, fixture, "recipients")
            if not recipients:
                return LiveGateIssue("missing_target", step.step_id, action_ref, "telegram.broadcast_message requires recipients")
            for chat_id in recipients:
                if chat_id not in allowed_chat_ids:
                    return LiveGateIssue(
                        "target_not_allowlisted",
                        step.step_id,
                        action_ref,
                        f"telegram.broadcast_message chat_id is not allowlisted: {chat_id}",
                    )
            return None
```

Add helpers below `_field_value`:

```python
def _field_values(step: WorkflowStep, fixture: Mapping[str, object], key: str) -> tuple[str, ...]:
    with_map = step.raw.get("with")
    fixture_input = fixture.get("input")
    primary = with_map if isinstance(with_map, Mapping) else {}
    fallback = fixture_input if isinstance(fixture_input, Mapping) else {}
    value = _resolve_input_ref(primary.get(key), fallback, fixture=fixture)
    if value is None:
        value = fallback.get(key)
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    scalar = _string_value(value)
    return (scalar,) if scalar is not None else ()
```

Extend `_resolve_input_ref` in `live_gate.py`:

```python
def _resolve_input_ref(
    value: object,
    fixture_input: Mapping[str, object],
    *,
    fixture: Mapping[str, object] | None = None,
) -> object:
    if isinstance(value, Mapping):
        input_ref = value.get("inputRef")
        if isinstance(input_ref, str):
            return fixture_input.get(input_ref)
    if isinstance(value, str) and fixture is not None:
        resolved = _resolve_step_output_template(value, fixture)
        if resolved is not None:
            return resolved
    return value
```

Update callers from `_resolve_input_ref(primary.get(key), fallback)` to `_resolve_input_ref(primary.get(key), fallback, fixture=fixture)`.

Add:

```python
def _resolve_step_output_template(value: str, fixture: Mapping[str, object]) -> object | None:
    match = STEP_OUTPUT_TEMPLATE_RE.match(value)
    if match is None:
        return None
    step_id, output_path = match.groups()
    steps = fixture.get("steps")
    if not isinstance(steps, Mapping):
        return None
    step_entry = steps.get(step_id)
    if not isinstance(step_entry, Mapping):
        return None
    current: object = step_entry.get("outputs")
    for part in output_path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _string_value(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
```

Copy `STEP_OUTPUT_TEMPLATE_RE` from `live_transports.py` or define the same regex near imports:

```python
import re

STEP_OUTPUT_TEMPLATE_RE = re.compile(r"^\s*\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_.-]+)\s*\}\}\s*$")
```

- [ ] **Step 4: Run focused and broader live runner tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_runner.py::test_live_production_gate_blocks_broadcast_recipient_outside_policy -q
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_runner.py -q
```

Expected: focused test passes, then all live runner tests pass.

- [ ] **Step 5: Commit Task 4**

```bash
git add appV2.4/tests/test_workflow_live_runner.py appV2.4/appv24/workflows/live_gate.py
git commit -m "feat(appv24): gate telegram broadcast targets"
```

## Task 5: Prove Brave, Subscriber, LLM, Broadcast Phase Execution

**Files:**
- Modify: `appV2.4/tests/test_workflow_phase_runner.py`

- [ ] **Step 1: Write the failing golden phase-runner test**

Add this test next to `test_phase_runner_runs_mixed_brave_local_and_live_production_from_approval_step`:

```python
def test_phase_runner_runs_ai_news_subscriber_broadcast_from_connector_interfaces(tmp_path: Path) -> None:
    package = _write_ai_news_subscriber_broadcast_package(tmp_path)
    run_root = tmp_path / ".workflow-runs"
    seen_live_read_requests: list[BrokerRequest] = []

    class BraveTransport(LiveWorkflowTransport):
        def invoke(self, request: BrokerRequest) -> BrokerResult:
            seen_live_read_requests.append(request)
            return BrokerResult(
                status="live-read",
                provider_id="fake-brave",
                outputs={
                    "items": [{"title": "AI model update", "url": "https://example.test/ai"}],
                    "source_count": 1,
                    "query": "artificial intelligence news today",
                },
            )

    class SubscriberTransport(LiveWorkflowTransport):
        def invoke(self, request: BrokerRequest) -> BrokerResult:
            seen_live_read_requests.append(request)
            return BrokerResult(
                status="live-read",
                provider_id="fake-telegram",
                outputs={
                    "recipients": ["admin-chat", "ops-chat"],
                    "recipient_count": 2,
                    "audience_hash": "audience:test",
                },
            )

    def summarize(request: BrokerRequest) -> BrokerResult:
        assert request.fixture["handoff"]["outputs"]["search_ai_news"]["items"][0]["title"] == "AI model update"
        assert request.fixture["handoff"]["outputs"]["list_subscribers"]["recipients"] == ["admin-chat", "ops-chat"]
        return BrokerResult(
            status="simulated",
            provider_id="phase-test",
            outputs={"summary": "AI model update: https://example.test/ai", "raw_response": {"ok": True}},
        )

    approval_report = run_workflow_live_read_to_approval(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            run_root=run_root,
            connector_allowlist=("brave.search", "telegram.list_subscribers"),
        ),
        live_read_broker=LiveWorkflowBroker(
            {
                "brave.search": BraveTransport(),
                "telegram.list_subscribers": SubscriberTransport(),
            },
            mode="live-read",
        ),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "llm": CredentialScope(available=True, scopes=()),
                "telegram": CredentialScope(available=True, scopes=("telegram:subscribers:read", "telegram:send")),
            }
        ),
        local_broker=LocalWorkflowBroker({"llm.summarize": summarize}),
    )

    assert approval_report.status == "approval-required"
    assert [request.connector_action for request in seen_live_read_requests] == [
        "brave.search",
        "telegram.list_subscribers",
    ]

    store = WorkflowRunStore(run_root)
    request = store.read_approval_requests(approval_report.local_run_id)[0]
    assert request.step_id == "send_digest"
    assert request.connector_action == "telegram.broadcast_message"
    store.record_approval_decision(
        approval_report.local_run_id,
        request.approval_request_id,
        ApprovalDecision(approved=True, package_fingerprint=request.package_fingerprint, decided_by="Lewis"),
    )

    class BroadcastTransport(LiveWorkflowTransport):
        def __init__(self) -> None:
            self.requests: list[BrokerRequest] = []

        def invoke(self, request: BrokerRequest) -> BrokerResult:
            self.requests.append(request)
            return BrokerResult(
                status="live-production",
                provider_id="fake-telegram",
                outputs={"sent_count": 2, "message_ids": ["msg-1", "msg-2"], "chat_ids": ["admin-chat", "ops-chat"]},
            )

    production_transport = BroadcastTransport()
    production_report = run_workflow_approved_live_production(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            run_root=run_root,
            connector_allowlist=("telegram.broadcast_message",),
            operator_env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat,ops-chat"},
        ),
        approval_run_id=approval_report.local_run_id,
        live_production_broker=LiveWorkflowBroker(
            {"telegram.broadcast_message": production_transport},
            mode="live-production",
            production_enabled=True,
        ),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "llm": CredentialScope(available=True, scopes=()),
                "telegram": CredentialScope(available=True, scopes=("telegram:subscribers:read", "telegram:send")),
            }
        ),
    )

    assert production_report.status == "passed"
    assert production_report.live_production_report is not None
    assert [trace.step_id for trace in production_report.live_production_report.step_trace] == ["send_digest", "done"]
    assert production_transport.requests[0].connector_action == "telegram.broadcast_message"
    assert production_transport.requests[0].fixture["steps"]["list_subscribers"]["outputs"]["recipients"] == [
        "admin-chat",
        "ops-chat",
    ]
```

Add this complete helper in the same file. Keep both live-read connector actions before the local LLM step so `run_workflow_live_read_to_approval()` can execute the live-read prefix and then resume locally:

```python
def _write_ai_news_subscriber_broadcast_package(root: Path) -> Path:
    package = root / "ai-news-telegram-broadcast"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "ai-news-telegram-broadcast",
                "description": "Search AI news, summarize it, and broadcast to Telegram subscribers.",
                "design": {
                    "intent": "Send a Telegram digest of recent AI news to registered subscribers.",
                    "trigger": "Manual operator run.",
                    "inputs": ["Telegram subscriber audience"],
                    "outputs": ["Telegram broadcast result"],
                    "assumptions": ["Brave and Telegram connectors are configured."],
                    "side_effects": ["Send Telegram broadcast after approval."],
                    "approval_gates": ["approve_telegram_broadcast"],
                    "failure_modes": ["Search, summarization, subscriber listing, or broadcast fails."],
                    "rollback": ["Review Telegram broadcast manually."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "search_ai_news",
                "steps": [
                    {
                        "id": "search_ai_news",
                        "uses": "brave.search",
                        "with": {"query": "artificial intelligence news today", "count": 5},
                        "next": "list_subscribers",
                    },
                    {
                        "id": "list_subscribers",
                        "uses": "telegram.list_subscribers",
                        "with": {"audience": "default"},
                        "next": "summarize_digest",
                    },
                    {
                        "id": "summarize_digest",
                        "uses": "llm.summarize",
                        "with": {
                            "text": "{{ steps.search_ai_news.outputs.items }}",
                            "output_contract": "summary_v1",
                        },
                        "next": "send_digest",
                    },
                    {
                        "id": "send_digest",
                        "uses": "telegram.broadcast_message",
                        "side_effect_class": "send",
                        "approval": "approve_telegram_broadcast",
                        "with": {
                            "recipients": "{{ steps.list_subscribers.outputs.recipients }}",
                            "audience_hash": "{{ steps.list_subscribers.outputs.audience_hash }}",
                            "text": "{{ steps.summarize_digest.outputs.summary }}",
                        },
                        "next": "done",
                    },
                    {"id": "done", "type": "terminal"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.connectors/v1",
                "connectors": [
                    {
                        "id": "brave",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "search",
                                "side_effect": False,
                                "required_scopes": ["brave:search"],
                                "outputs": {"allowed": ["items", "source_count", "query"]},
                            }
                        ],
                    },
                    {
                        "id": "llm",
                        "adapter": "llm",
                        "actions": [
                            {"id": "summarize", "side_effect": False, "outputs": {"allowed": ["summary", "raw_response"]}}
                        ],
                    },
                    {
                        "id": "telegram",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "list_subscribers",
                                "side_effect": False,
                                "required_scopes": ["telegram:subscribers:read"],
                                "outputs": {"allowed": ["recipients", "recipient_count", "audience_hash"]},
                            },
                            {
                                "id": "broadcast_message",
                                "side_effect": True,
                                "side_effect_class": "send",
                                "execution_mode": "brokered",
                                "approval_required": True,
                                "dry_run_supported": True,
                                "required_scopes": ["telegram:send"],
                                "idempotency": {"key_fields": ["audience_hash", "text"]},
                                "outputs": {"allowed": ["sent_count", "message_ids", "chat_ids"]},
                            },
                        ],
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.permissions/v1",
                "permissions": [
                    {"connector": "brave", "actions": ["search"], "scopes": ["brave:search"]},
                    {"connector": "llm", "actions": ["summarize"], "scopes": []},
                    {
                        "connector": "telegram",
                        "actions": ["list_subscribers", "broadcast_message"],
                        "scopes": ["telegram:subscribers:read", "telegram:send"],
                    },
                ],
                "approvals": [{"id": "approve_telegram_broadcast", "mode": "explicit", "required_for": ["send_digest"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    fixture_text = "input:\n  audience: default\n"
    (fixtures / "happy_path.yaml").write_text(fixture_text, encoding="utf-8")
    (fixtures / "rejection_path.yaml").write_text(fixture_text, encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nReview Telegram broadcast manually.\n", encoding="utf-8")
    return package
```

- [ ] **Step 2: Run the golden phase-runner test to verify red**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_phase_runner.py::test_phase_runner_runs_ai_news_subscriber_broadcast_from_connector_interfaces -q
```

Expected: failure until live plan, gate, and transports recognize the new connector actions.

- [ ] **Step 3: Update live plan transport sets**

In `appV2.4/appv24/workflows/live_plan.py`, update:

```python
BUILTIN_LIVE_READ_TRANSPORTS = frozenset(
    {
        "github.list_dependabot_alerts",
        "brave.search",
        "source.fetch_document",
        "source.fetch_items",
        "telegram.list_subscribers",
    }
)
BUILTIN_LIVE_PRODUCTION_TRANSPORTS = frozenset({"telegram.send_message", "telegram.broadcast_message"})
```

- [ ] **Step 4: Run phase-runner tests to verify green**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_phase_runner.py -q
```

Expected: all phase runner tests pass.

- [ ] **Step 5: Commit Task 5**

```bash
git add appV2.4/tests/test_workflow_phase_runner.py appV2.4/appv24/workflows/live_plan.py
git commit -m "feat(appv24): run ai news broadcast through connector phases"
```

## Task 6: Make TUI And CLI Reports Guide The Golden Path

**Files:**
- Modify: `appV2.4/tests/test_tui.py`
- Modify: `appV2.4/tests/test_cli.py`

- [ ] **Step 1: Write failing TUI live-plan test**

Add this helper near the existing package helpers in both `appV2.4/tests/test_tui.py` and `appV2.4/tests/test_cli.py`:

```python
def _write_ai_news_subscriber_broadcast_package(root):
    package = root / "ai-news-telegram-broadcast"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "ai-news-telegram-broadcast",
                "description": "Search AI news, summarize it, and broadcast to Telegram subscribers.",
                "design": {
                    "intent": "Send a Telegram digest of recent AI news to registered subscribers.",
                    "trigger": "Manual operator run.",
                    "inputs": ["Telegram subscriber audience"],
                    "outputs": ["Telegram broadcast result"],
                    "assumptions": ["Brave and Telegram connectors are configured."],
                    "side_effects": ["Send Telegram broadcast after approval."],
                    "approval_gates": ["approve_telegram_broadcast"],
                    "failure_modes": ["Search, summarization, subscriber listing, or broadcast fails."],
                    "rollback": ["Review Telegram broadcast manually."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "search_ai_news",
                "steps": [
                    {
                        "id": "search_ai_news",
                        "uses": "brave.search",
                        "with": {"query": "artificial intelligence news today", "count": 5},
                        "next": "list_subscribers",
                    },
                    {
                        "id": "list_subscribers",
                        "uses": "telegram.list_subscribers",
                        "with": {"audience": "default"},
                        "next": "summarize_digest",
                    },
                    {
                        "id": "summarize_digest",
                        "uses": "llm.summarize",
                        "with": {"text": "{{ steps.search_ai_news.outputs.items }}", "output_contract": "summary_v1"},
                        "next": "send_digest",
                    },
                    {
                        "id": "send_digest",
                        "uses": "telegram.broadcast_message",
                        "side_effect_class": "send",
                        "approval": "approve_telegram_broadcast",
                        "with": {
                            "recipients": "{{ steps.list_subscribers.outputs.recipients }}",
                            "audience_hash": "{{ steps.list_subscribers.outputs.audience_hash }}",
                            "text": "{{ steps.summarize_digest.outputs.summary }}",
                        },
                        "next": "done",
                    },
                    {"id": "done", "type": "terminal"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.connectors/v1",
                "connectors": [
                    {
                        "id": "brave",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "search",
                                "side_effect": False,
                                "required_scopes": ["brave:search"],
                                "outputs": {"allowed": ["items", "source_count", "query"]},
                            }
                        ],
                    },
                    {
                        "id": "llm",
                        "adapter": "llm",
                        "actions": [
                            {"id": "summarize", "side_effect": False, "outputs": {"allowed": ["summary", "raw_response"]}}
                        ],
                    },
                    {
                        "id": "telegram",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "list_subscribers",
                                "side_effect": False,
                                "required_scopes": ["telegram:subscribers:read"],
                                "outputs": {"allowed": ["recipients", "recipient_count", "audience_hash"]},
                            },
                            {
                                "id": "broadcast_message",
                                "side_effect": True,
                                "side_effect_class": "send",
                                "execution_mode": "brokered",
                                "approval_required": True,
                                "dry_run_supported": True,
                                "required_scopes": ["telegram:send"],
                                "idempotency": {"key_fields": ["audience_hash", "text"]},
                                "outputs": {"allowed": ["sent_count", "message_ids", "chat_ids"]},
                            },
                        ],
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.permissions/v1",
                "permissions": [
                    {"connector": "brave", "actions": ["search"], "scopes": ["brave:search"]},
                    {"connector": "llm", "actions": ["summarize"], "scopes": []},
                    {
                        "connector": "telegram",
                        "actions": ["list_subscribers", "broadcast_message"],
                        "scopes": ["telegram:subscribers:read", "telegram:send"],
                    },
                ],
                "approvals": [{"id": "approve_telegram_broadcast", "mode": "explicit", "required_for": ["send_digest"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    fixture_text = "input:\n  audience: default\n"
    (fixtures / "happy_path.yaml").write_text(fixture_text, encoding="utf-8")
    (fixtures / "rejection_path.yaml").write_text(fixture_text, encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nReview Telegram broadcast manually.\n", encoding="utf-8")
    return package
```

Add this test near the existing `/workflow-live-plan` tests in `appV2.4/tests/test_tui.py`:

```python
def test_interactive_mode_workflow_live_plan_shows_subscriber_broadcast_commands(tmp_path) -> None:
    package = _write_ai_news_subscriber_broadcast_package(tmp_path)
    terminal = FakeTerminal(columns=160, rows=80)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.profile_id = "workflow-designer-lite"
    inputs = iter([f"/workflow-live-plan {package.name} happy_path.yaml", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    assert mode.run() == 0

    rendered = strip_ansi("\n".join(app.tui.render(160)))

    assert "telegram.list_subscribers" in rendered
    assert "telegram.broadcast_message" in rendered
    assert "/workflow-run-live-read-to-approval" in rendered
    assert "--allow brave.search,telegram.list_subscribers" in rendered
    assert "/workflow-run-live-production" in rendered
    assert "--allow telegram.broadcast_message" in rendered
```

Add this equivalent CLI test in `appV2.4/tests/test_cli.py` near `test_cli_plain_workflow_live_plan_prints_phases_without_model`:

```python
def test_cli_plain_workflow_live_plan_shows_subscriber_broadcast_commands(monkeypatch, tmp_path, capsys) -> None:
    package = _write_ai_news_subscriber_broadcast_package(tmp_path)
    prompts = iter([f"/workflow-live-plan {package.name} happy_path.yaml", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow live plan command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    rendered = captured.out

    assert exit_code == 0
    assert "telegram.list_subscribers" in rendered
    assert "telegram.broadcast_message" in rendered
    assert "/workflow-run-live-read-to-approval" in rendered
    assert "--allow brave.search,telegram.list_subscribers" in rendered
    assert "/workflow-run-live-production" in rendered
    assert "--allow telegram.broadcast_message" in rendered
```

- [ ] **Step 2: Run TUI/CLI tests to verify red**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_tui.py::test_interactive_mode_workflow_live_plan_shows_subscriber_broadcast_commands -q
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py::test_cli_plain_workflow_live_plan_shows_subscriber_broadcast_commands -q
```

Expected: failure until the live plan command output includes the new connector actions and allowlists.

- [ ] **Step 3: Confirm live plan command generation**

`appV2.4/appv24/workflows/live_plan.py` already has the correct `_live_read_to_approval_refs()` traversal shape. Confirm it still matches this code after Task 5 adds the Telegram transport sets:

Use this behavior:

```text
phase allowlist for the golden path = brave.search,telegram.list_subscribers
production allowlist = telegram.broadcast_message
```

Keep the implementation generic:

```python
def _live_read_to_approval_refs(ir: WorkflowIR, fixture: Mapping[str, Any]) -> tuple[str, ...]:
    catalog = builtin_connector_catalog()
    refs: list[str] = []
    saw_live_read = False
    current = ir.entrypoint
    transitions = 0

    while current:
        transitions += 1
        if transitions > 200:
            break
        step = ir.steps.get(current)
        if step is None or step.kind is StepKind.TERMINAL:
            break
        if step.kind is StepKind.DECISION:
            _selected_branch, next_step = _select_branch(step, fixture)
            if next_step is None:
                break
            current = next_step
            continue
        if step.kind is not StepKind.ACTION or step.uses is None:
            current = step.next_step
            continue
        action_ref = step.uses.dotted
        metadata = catalog.actions.get(action_ref)
        if metadata is not None and metadata.live_read_eligible:
            refs.append(action_ref)
            saw_live_read = True
            current = step.next_step
            continue
        if saw_live_read:
            refs.append(action_ref)
            break
        current = step.next_step

    return tuple(dict.fromkeys(refs)) if saw_live_read and len(refs) > 1 else ()
```

- [ ] **Step 4: Run TUI and CLI focused tests to verify green**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_tui.py::test_interactive_mode_workflow_live_plan_shows_subscriber_broadcast_commands -q
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py::test_cli_plain_workflow_live_plan_shows_subscriber_broadcast_commands -q
```

Expected: both focused tests pass.

- [ ] **Step 5: Commit Task 6**

```bash
git add appV2.4/tests/test_tui.py appV2.4/tests/test_cli.py
git commit -m "feat(appv24): guide connector workflow live plan"
```

## Task 7: Add A Generated-Draft Regression For The End-User Prompt Shape

**Files:**
- Modify: `appV2.4/tests/test_workflow_connector_contracts.py`
- Modify: `appV2.4/tests/test_workflow_packages.py`
- Modify: `appV2.4/appv24/workflows/prompt_fragments.py`

- [ ] **Step 1: Write failing prompt-shape regression**

Add a small static draft validation test to `appV2.4/tests/test_workflow_packages.py`:

```python
def test_ai_news_telegram_registered_users_package_uses_connector_interfaces(tmp_path: Path) -> None:
    package = tmp_path / "ai-news-telegram"
    package.mkdir()
    (package / "workflow.yaml").write_text(
        """
version: appv24.workflow/v1
name: ai-news-telegram-digest
description: Search recent AI news, summarize it, and broadcast to Telegram subscribers.
design:
  intent: Send a Telegram digest of recent AI news to registered subscribers.
  trigger: Manual run.
  inputs:
    - Telegram subscriber connector audience.
  outputs:
    - Telegram broadcast result.
  assumptions:
    - Subscriber connector is configured.
  side_effects:
    - Send Telegram broadcast after approval.
  approval_gates:
    - approve_telegram_broadcast
  failure_modes:
    - Search or broadcast fails.
  rollback:
    - Review Telegram broadcast manually.
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
      count: 10
    next: list_subscribers
  - id: list_subscribers
    uses: telegram.list_subscribers
    with:
      audience: default
    next: summarize_digest
  - id: summarize_digest
    uses: llm.summarize
    with:
      text: "{{ steps.search_ai_news.outputs.items }}"
      output_contract: summary_v1
    next: send_digest
  - id: send_digest
    uses: telegram.broadcast_message
    side_effect_class: send
    approval: approve_telegram_broadcast
    with:
      recipients: "{{ steps.list_subscribers.outputs.recipients }}"
      audience_hash: "{{ steps.list_subscribers.outputs.audience_hash }}"
      text: "{{ steps.summarize_digest.outputs.summary }}"
    next: done
  - id: done
    type: terminal
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        """
version: appv24.connectors/v1
connectors:
  - id: brave
    adapter: direct_api
    actions:
      - id: search
        side_effect: false
        required_scopes: [brave:search]
        outputs:
          allowed: [items, source_count, query]
  - id: llm
    adapter: llm
    actions:
      - id: summarize
        side_effect: false
        outputs:
          allowed: [summary, raw_response]
  - id: telegram
    adapter: direct_api
    actions:
      - id: list_subscribers
        side_effect: false
        required_scopes: [telegram:subscribers:read]
        outputs:
          allowed: [recipients, recipient_count, audience_hash]
      - id: broadcast_message
        side_effect: true
        side_effect_class: send
        execution_mode: brokered
        approval_required: true
        dry_run_supported: true
        required_scopes: [telegram:send]
        idempotency:
          key_fields: [audience_hash, text]
        outputs:
          allowed: [sent_count, message_ids, chat_ids]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        """
version: appv24.permissions/v1
permissions:
  - connector: brave
    actions: [search]
    scopes: [brave:search]
  - connector: llm
    actions: [summarize]
    scopes: []
  - connector: telegram
    actions: [list_subscribers, broadcast_message]
    scopes: [telegram:subscribers:read, telegram:send]
approvals:
  - id: approve_telegram_broadcast
    mode: explicit
    required_for: [send_digest]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    fixture = "input:\n  audience: default\n"
    (fixtures / "happy_path.yaml").write_text(fixture, encoding="utf-8")
    (fixtures / "rejection_path.yaml").write_text(fixture, encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nReview Telegram broadcast manually.\n", encoding="utf-8")

    result = validate_workflow_package(package)

    assert result.ok
```

If `validate_workflow_package` is not imported in the file, add:

```python
from appv24.workflows.validator import validate_workflow_package
```

- [ ] **Step 2: Run package test to verify red**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_ai_news_telegram_registered_users_package_uses_connector_interfaces -q
```

Expected: failure until the new connector actions are accepted throughout semantic validation.

- [ ] **Step 3: Strengthen prompt catalog guidance**

In `appV2.4/appv24/workflows/prompt_fragments.py`, ensure `render_capability_catalog_fragment()` includes:

```python
        "- Registered-user delivery must be modeled as connector interfaces: telegram.list_subscribers then telegram.broadcast_message.",
        "- Approval for registered-user Telegram delivery belongs on telegram.broadcast_message, not on a separate manual approval step.",
```

- [ ] **Step 4: Run package and prompt tests to verify green**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_ai_news_telegram_registered_users_package_uses_connector_interfaces appV2.4/tests/test_workflow_connector_contracts.py::test_capability_prompt_uses_slim_contract_summaries -q
```

Expected: both focused tests pass.

- [ ] **Step 5: Commit Task 7**

```bash
git add appV2.4/tests/test_workflow_packages.py appV2.4/tests/test_workflow_connector_contracts.py appV2.4/appv24/workflows/prompt_fragments.py
git commit -m "test(appv24): lock ai news telegram connector package shape"
```

## Task 8: Verification And Human TUI Scenario

**Files:**
- No planned code edits.
- Uses `.env` without printing secrets.

- [ ] **Step 1: Run focused workflow tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_connector_contracts.py \
  appV2.4/tests/test_workflow_local_artifacts.py \
  appV2.4/tests/test_workflow_live_transports.py \
  appV2.4/tests/test_workflow_live_runner.py \
  appV2.4/tests/test_workflow_phase_runner.py \
  appV2.4/tests/test_workflow_packages.py \
  appV2.4/tests/test_tui.py \
  appV2.4/tests/test_cli.py
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full appv24 Python tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Expected: all appv24 Python tests pass.

- [ ] **Step 3: Run appv24 CLI package tests**

Run:

```bash
npm --prefix packages/appv24-cli test
npm --prefix packages/appv24-cli run pack:dry-run
git diff --check
```

Expected: appv24 CLI tests pass, dry-run pack succeeds, and diff check is clean.

- [ ] **Step 4: Run actual human-style TUI scenario**

Start TUI with `.env` loaded without printing secrets:

```bash
set -a
source .env >/dev/null 2>&1
set +a
PYTHONPATH=appV2.4 .venv/bin/python -m appv24.cli --profile workflow-designer-lite --cwd "$PWD"
```

Type this as the user prompt:

```text
Build me a workflow that scrapes today's AI news, summarizes it, and sends it from my Telegram bot to registered users.
```

Then type commands in the TUI, using the actual saved package path and run IDs shown by the TUI:

```text
/workflow-save tmp/appv24-live-scenarios/generated-ai-news-telegram-mvp
/workflow-live-plan tmp/appv24-live-scenarios/generated-ai-news-telegram-mvp happy_path.yaml
/workflow-run-live-read-to-approval tmp/appv24-live-scenarios/generated-ai-news-telegram-mvp happy_path.yaml --allow brave.search,telegram.list_subscribers
/workflow-approvals tmp/appv24-live-scenarios/generated-ai-news-telegram-mvp <approval-run-id>
/workflow-approve tmp/appv24-live-scenarios/generated-ai-news-telegram-mvp <approval-run-id> <approval-request-id>
/workflow-live-ready live-production tmp/appv24-live-scenarios/generated-ai-news-telegram-mvp happy_path.yaml <approval-run-id> --allow telegram.broadcast_message
/workflow-run-live-production tmp/appv24-live-scenarios/generated-ai-news-telegram-mvp happy_path.yaml <approval-run-id> --allow telegram.broadcast_message
/exit
```

Expected:

- Generated draft validates and passes quality gate.
- Saved package uses `telegram.list_subscribers` and `telegram.broadcast_message`.
- Live-read runs Brave and Telegram subscriber listing through connector interfaces.
- Approval request is for the `telegram.broadcast_message` step.
- Production run starts at the approved broadcast step.
- Telegram send either succeeds or blocks with one connector-interface reason such as missing env, empty subscribers, or target policy mismatch.
- No secret values are printed in final notes.

- [ ] **Step 5: Commit verification notes if runtime docs changed**

If verification changes only runtime artifacts under ignored `tmp/`, do not commit those artifacts.

If documentation is updated with final operator notes, commit only tracked docs:

```bash
git add docs/superpowers/plans/2026-07-08-appv24-connector-interface-workflow-mvp.md
git commit -m "docs(appv24): plan connector interface workflow mvp"
```
