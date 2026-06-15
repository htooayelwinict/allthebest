# AppV2.2 Pi-Hermes Extension Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a clean AppV2.2 runtime where the core agent loop is Pi-style, domain behavior lives in skill-linked extensions, and context governance uses Hermes gateway hygiene plus in-loop structured compression.

**Architecture:** AppV2.2 is a new implementation under `appV2.2/appv22`, not an AppV2.1 refactor. Runtime core owns orchestration, state reduction, generic decision routing, provider calls, context lifecycle, and generic receipts; extensions register skill cards, tools, planners, mutation policies, mutation executors, verifiers, and artifact schemas. File management is only the first extension and must be removable without editing runtime core.

**Tech Stack:** Python 3.13, dataclasses, pytest, existing AppV2.1 appv2-env/OpenRouter provider compatibility, JSON-schema-shaped tool definitions.

---

## Non-Negotiable Architecture Rules

- Runtime core must not import from `appv22.extensions.file_management.*`.
- Runtime core must not contain file path preservation rules, workspace manifest assumptions, or file move/write implementations.
- Planners are extension capabilities, not tools.
- Tool broker executes registered tools only; extensions register tool definitions and handlers.
- Mutation policy and mutation execution are extension capabilities; core only issues generic leases/receipts.
- Verifiers are extension capabilities resolved by verifier ID.
- Skill cards link `planner_id`, `mutation_policy_id`, `mutation_executor_id`, `verifier_id`, `tool_ids`, and `artifact_schema_ids`.
- Hermes gateway guard runs before every provider call at the 85 percent threshold.
- Hermes in-loop compressor runs in the agent loop at the 50 percent threshold using prune, boundary detection, structured summary, and assembly.
- Prompt context selection uses the semantic pre-turn mode, not incidental `THINK` prompt-building state.
- Live acceptance requires a vague-prompt complex file-management probe with `appv2-env`.

---

## Target File Structure

```text
appV2.2/appv22/
  __init__.py
  state/events.py
  state/models.py
  runtime/agent_loop.py
  runtime/capabilities.py
  runtime/decisions.py
  runtime/reducer.py
  runtime/services.py
  runtime/state_machine.py
  context/budget.py
  context/compressor.py
  context/gateway_guard.py
  context/prompt_builder.py
  context/selector.py
  context/summaries.py
  tools/broker.py
  tools/definitions.py
  tools/registry.py
  extensions/base.py
  extensions/registry.py
  extensions/file_management/extension.py
  extensions/file_management/skills.py
  extensions/file_management/tools.py
  extensions/file_management/planner.py
  extensions/file_management/mutation_policy.py
  extensions/file_management/mutation_executor.py
  extensions/file_management/verifier.py
  extensions/file_management/schemas.py
  providers/appv2_env.py
  providers/deterministic.py
scripts/live_appv22_complex_vague_file_management_probe.py
tests/appv22/
```

---

## Task 1: Domain-Free Runtime State and Decisions

**Files:**
- Create: `appV2.2/appv22/state/models.py`
- Create: `appV2.2/appv22/state/events.py`
- Create: `appV2.2/appv22/runtime/decisions.py`
- Create: `appV2.2/appv22/state/__init__.py`
- Create: `appV2.2/appv22/runtime/__init__.py`
- Create: `appV2.2/appv22/__init__.py`
- Test: `tests/appv22/test_runtime_state.py`

- [ ] **Step 1: Write failing tests**

```python
from appv22.runtime.decisions import RuntimeDecision
from appv22.state.events import RuntimeEvent
from appv22.state.models import AgentState, RequestEnvelope


def test_agent_state_has_no_domain_fields():
    state = AgentState(session_id="sess", run_id="run", request=RequestEnvelope("req", "clean this", "."))

    assert state.mode == "START"
    assert state.active_skill_ids == []
    assert state.active_extension_ids == []
    assert "manifest" not in state.__dict__
    assert "file_policy" not in state.__dict__


def test_runtime_decision_is_generic():
    decision = RuntimeDecision(kind="plan", reason="use active extension planner")
    event = RuntimeEvent("DecisionProposed", decision.to_dict())

    assert event.payload["kind"] == "plan"
    assert event.event_type == "DecisionProposed"
```

- [ ] **Step 2: Run test to verify failure**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_runtime_state.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'appv22'`.

- [ ] **Step 3: Implement minimal domain-free state**

```python
# appV2.2/appv22/state/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RuntimeMode = Literal["START", "THINK", "OBSERVE", "PLAN", "ACT", "VERIFY", "COMPACT", "PAUSE", "FINALIZE", "FAILED"]


@dataclass
class RequestEnvelope:
    request_id: str
    user_goal: str
    root_path: str
    constraints: list[str] = field(default_factory=list)


@dataclass
class AgentState:
    session_id: str
    run_id: str
    request: RequestEnvelope
    mode: RuntimeMode = "START"
    active_skill_ids: list[str] = field(default_factory=list)
    active_extension_ids: list[str] = field(default_factory=list)
    world_refs: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    runtime_plan: dict[str, Any] = field(default_factory=dict)
    mutation_leases: dict[str, dict[str, Any]] = field(default_factory=dict)
    mutation_receipts: dict[str, dict[str, Any]] = field(default_factory=dict)
    verification_receipts: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    conversation_messages: list[dict[str, Any]] = field(default_factory=list)
    context_summary: dict[str, Any] = field(default_factory=dict)
    terminal: bool = False
    result: dict[str, Any] | None = None
```

```python
# appV2.2/appv22/state/events.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class RuntimeEvent:
    event_type: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: f"evt_{uuid4().hex}")
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {"event_id": self.event_id, "event_type": self.event_type, "payload": self.payload, "timestamp": self.timestamp}
```

```python
# appV2.2/appv22/runtime/decisions.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

KNOWN_DECISION_KINDS = frozenset({"tool_call", "plan", "mutation_intent", "verify", "compact", "pause", "finalize"})


@dataclass(frozen=True)
class RuntimeDecision:
    kind: str
    reason: str
    payload: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    decision_id: str = field(default_factory=lambda: f"dec_{uuid4().hex}")

    def to_dict(self) -> dict[str, Any]:
        return {"decision_id": self.decision_id, "kind": self.kind, "reason": self.reason, "payload": self.payload, "evidence_refs": list(self.evidence_refs)}
```

```python
# appV2.2/appv22/__init__.py
# Runtime export is added after Task 7 creates agent_loop.py.
```

- [ ] **Step 4: Run tests and commit**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_runtime_state.py -q`

Expected: PASS.

Commit:

```bash
git add appV2.2/appv22 tests/appv22/test_runtime_state.py
git commit -m "feat(appv22): add domain-free runtime state"
```

---

## Task 2: Extension and Capability Registries

**Files:**
- Create: `appV2.2/appv22/extensions/base.py`
- Create: `appV2.2/appv22/extensions/registry.py`
- Create: `appV2.2/appv22/runtime/capabilities.py`
- Test: `tests/appv22/test_extension_registry.py`
- Test: `tests/appv22/test_architecture_boundaries.py`

- [ ] **Step 1: Write failing tests**

```python
from appv22.extensions.base import RuntimeExtension, SkillCard
from appv22.extensions.registry import ExtensionRegistry
from appv22.runtime.capabilities import CapabilityRegistry
from appv22.state.models import AgentState, RequestEnvelope


class DemoPlanner:
    capability_id = "demo.planner"


class DemoVerifier:
    capability_id = "demo.verifier"


class DemoMutationPolicy:
    capability_id = "demo.policy"


class DemoMutationExecutor:
    capability_id = "demo.executor"


class DemoExtension(RuntimeExtension):
    extension_id = "demo"

    def skill_cards(self):
        return [SkillCard("demo.cleanup", "demo", ("clean",), ("START", "PLAN", "ACT"), "Demo", "demo.planner", "demo.policy", "demo.executor", "demo.verifier", ("demo.inspect",), ("demo.schema",))]

    def register_capabilities(self, capabilities: CapabilityRegistry):
        capabilities.register_planner("demo.planner", DemoPlanner())
        capabilities.register_mutation_policy("demo.policy", DemoMutationPolicy())
        capabilities.register_mutation_executor("demo.executor", DemoMutationExecutor())
        capabilities.register_verifier("demo.verifier", DemoVerifier())


def test_extension_resolution_links_skill_to_capabilities():
    registry = ExtensionRegistry()
    capabilities = CapabilityRegistry()
    extension = DemoExtension()
    registry.register(extension)
    extension.register_capabilities(capabilities)
    state = AgentState("sess", "run", RequestEnvelope("req", "please clean", "."))

    resolved = registry.resolve_active(state)

    assert resolved.extension_ids == ["demo"]
    assert resolved.planner_ids == ["demo.planner"]
    assert capabilities.planner("demo.planner").capability_id == "demo.planner"
    assert capabilities.mutation_policy("demo.policy").capability_id == "demo.policy"
    assert capabilities.mutation_executor("demo.executor").capability_id == "demo.executor"
    assert capabilities.verifier("demo.verifier").capability_id == "demo.verifier"
```

```python
from pathlib import Path


def test_runtime_core_does_not_import_file_management_extension():
    runtime_files = list(Path("appV2.2/appv22/runtime").glob("*.py"))
    for path in runtime_files:
        text = path.read_text(encoding="utf-8")
        assert "extensions.file_management" not in text
```

- [ ] **Step 2: Run tests to verify failure**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_extension_registry.py tests/appv22/test_architecture_boundaries.py -q`

Expected: FAIL with missing extension and capability modules.

- [ ] **Step 3: Implement extension contracts and capability registry**

```python
# appV2.2/appv22/extensions/base.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from appv22.state.models import AgentState


@dataclass(frozen=True)
class SkillCard:
    skill_id: str
    extension_id: str
    triggers: tuple[str, ...]
    modes: tuple[str, ...]
    summary: str
    planner_id: str
    mutation_policy_id: str
    mutation_executor_id: str
    verifier_id: str
    tool_ids: tuple[str, ...]
    artifact_schema_ids: tuple[str, ...]

    def activates_for(self, state: AgentState) -> bool:
        text = state.request.user_goal.lower()
        return any(trigger in text for trigger in self.triggers)


class RuntimeExtension(Protocol):
    extension_id: str

    def skill_cards(self) -> list[SkillCard]:
        ...

    def register_capabilities(self, capabilities) -> None:
        ...
```

```python
# appV2.2/appv22/runtime/capabilities.py
from __future__ import annotations


class CapabilityRegistry:
    def __init__(self) -> None:
        self._planners: dict[str, object] = {}
        self._mutation_policies: dict[str, object] = {}
        self._mutation_executors: dict[str, object] = {}
        self._verifiers: dict[str, object] = {}
        self._artifact_schemas: dict[str, dict] = {}

    def register_planner(self, capability_id: str, planner: object) -> None:
        self._planners[capability_id] = planner

    def register_mutation_policy(self, capability_id: str, policy: object) -> None:
        self._mutation_policies[capability_id] = policy

    def register_mutation_executor(self, capability_id: str, executor: object) -> None:
        self._mutation_executors[capability_id] = executor

    def register_verifier(self, capability_id: str, verifier: object) -> None:
        self._verifiers[capability_id] = verifier

    def register_artifact_schema(self, schema_id: str, schema: dict) -> None:
        self._artifact_schemas[schema_id] = schema

    def planner(self, capability_id: str) -> object:
        return self._planners[capability_id]

    def mutation_policy(self, capability_id: str) -> object:
        return self._mutation_policies[capability_id]

    def mutation_executor(self, capability_id: str) -> object:
        return self._mutation_executors[capability_id]

    def verifier(self, capability_id: str) -> object:
        return self._verifiers[capability_id]
```

```python
# appV2.2/appv22/extensions/registry.py
from __future__ import annotations

from dataclasses import dataclass

from appv22.extensions.base import RuntimeExtension, SkillCard
from appv22.state.models import AgentState


@dataclass(frozen=True)
class ResolvedExtensions:
    extension_ids: list[str]
    skill_cards: list[SkillCard]
    tool_ids: list[str]
    planner_ids: list[str]
    mutation_policy_ids: list[str]
    mutation_executor_ids: list[str]
    verifier_ids: list[str]
    artifact_schema_ids: list[str]


class ExtensionRegistry:
    def __init__(self) -> None:
        self._extensions: dict[str, RuntimeExtension] = {}

    def register(self, extension: RuntimeExtension) -> None:
        self._extensions[extension.extension_id] = extension

    def resolve_active(self, state: AgentState) -> ResolvedExtensions:
        cards = [card for extension in self._extensions.values() for card in extension.skill_cards() if card.activates_for(state)]
        return ResolvedExtensions(
            extension_ids=sorted({card.extension_id for card in cards}),
            skill_cards=cards,
            tool_ids=sorted({tool_id for card in cards for tool_id in card.tool_ids}),
            planner_ids=sorted({card.planner_id for card in cards}),
            mutation_policy_ids=sorted({card.mutation_policy_id for card in cards}),
            mutation_executor_ids=sorted({card.mutation_executor_id for card in cards}),
            verifier_ids=sorted({card.verifier_id for card in cards}),
            artifact_schema_ids=sorted({schema_id for card in cards for schema_id in card.artifact_schema_ids}),
        )
```

- [ ] **Step 4: Run tests and commit**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_extension_registry.py tests/appv22/test_architecture_boundaries.py -q`

Expected: PASS.

Commit:

```bash
git add appV2.2/appv22/extensions appV2.2/appv22/runtime/capabilities.py tests/appv22/test_extension_registry.py tests/appv22/test_architecture_boundaries.py
git commit -m "feat(appv22): add extension capability registries"
```

---

## Task 3: Registry-Backed Tool Broker With Schema Checks

**Files:**
- Create: `appV2.2/appv22/tools/definitions.py`
- Create: `appV2.2/appv22/tools/registry.py`
- Create: `appV2.2/appv22/tools/broker.py`
- Test: `tests/appv22/test_tool_broker_registry.py`

- [ ] **Step 1: Write failing tests**

```python
from appv22.tools.broker import ToolBroker
from appv22.tools.definitions import ToolDefinition
from appv22.tools.registry import ToolRegistry


def test_tool_broker_executes_registered_active_tool(tmp_path):
    registry = ToolRegistry()
    registry.register(
        ToolDefinition("demo.echo", "observe", "low", {"required": ["message"]}, {"type": "object"}, "runtime_observed", "Echo."),
        lambda args, _ctx: {"status": "completed", "message": args["message"]},
    )
    broker = ToolBroker(registry=registry, root_path=tmp_path)

    result = broker.execute("demo.echo", {"message": "hello"}, active_tool_ids=["demo.echo"])

    assert result["status"] == "completed"
    assert result["payload"]["message"] == "hello"
    assert result["payload_ref"].startswith("world://tool_payload/")


def test_tool_broker_denies_missing_required_argument(tmp_path):
    registry = ToolRegistry()
    registry.register(
        ToolDefinition("demo.echo", "observe", "low", {"required": ["message"]}, {"type": "object"}, "runtime_observed", "Echo."),
        lambda args, _ctx: {"status": "completed", "message": args["message"]},
    )
    broker = ToolBroker(registry=registry, root_path=tmp_path)

    result = broker.execute("demo.echo", {}, active_tool_ids=["demo.echo"])

    assert result["status"] == "denied"
    assert result["payload"]["errors"] == ["missing_argument:message"]
```

- [ ] **Step 2: Run test to verify failure**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_tool_broker_registry.py -q`

Expected: FAIL with missing tool modules.

- [ ] **Step 3: Implement broker**

```python
# appV2.2/appv22/tools/definitions.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolDefinition:
    tool_id: str
    category: str
    risk_level: str
    argument_schema: dict[str, Any]
    result_schema: dict[str, Any]
    trust: str
    guidance: str
```

```python
# appV2.2/appv22/tools/registry.py
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from appv22.tools.definitions import ToolDefinition

ToolHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        self._definitions[definition.tool_id] = definition
        self._handlers[definition.tool_id] = handler

    def definition(self, tool_id: str) -> ToolDefinition | None:
        return self._definitions.get(tool_id)

    def handler(self, tool_id: str) -> ToolHandler | None:
        return self._handlers.get(tool_id)
```

```python
# appV2.2/appv22/tools/broker.py
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from appv22.tools.registry import ToolRegistry


class ToolBroker:
    def __init__(self, *, registry: ToolRegistry, root_path: str | Path) -> None:
        self.registry = registry
        self.root_path = Path(root_path).resolve()

    def execute(self, tool_id: str, arguments: dict[str, Any], *, active_tool_ids: list[str]) -> dict[str, Any]:
        if tool_id not in set(active_tool_ids):
            return self._envelope(tool_id, "denied", {"errors": [f"inactive_tool:{tool_id}"]}, create_ref=False)
        definition = self.registry.definition(tool_id)
        handler = self.registry.handler(tool_id)
        if definition is None or handler is None:
            return self._envelope(tool_id, "denied", {"errors": [f"unknown_tool:{tool_id}"]}, create_ref=False)
        errors = [f"missing_argument:{key}" for key in definition.argument_schema.get("required", []) if key not in arguments]
        if errors:
            return self._envelope(tool_id, "denied", {"errors": errors}, create_ref=False)
        payload = handler(arguments, {"root_path": self.root_path})
        status = str(payload.pop("status", "completed"))
        return self._envelope(tool_id, status, payload, create_ref=status == "completed")

    def _envelope(self, tool_id: str, status: str, payload: dict[str, Any], *, create_ref: bool) -> dict[str, Any]:
        result_id = f"toolres_{uuid4().hex}"
        return {"tool_result_id": result_id, "tool_id": tool_id, "status": status, "payload": payload, "payload_ref": f"world://tool_payload/{result_id}" if create_ref else "", "evidence_refs": []}
```

- [ ] **Step 4: Run tests and commit**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_tool_broker_registry.py -q`

Expected: PASS.

Commit:

```bash
git add appV2.2/appv22/tools tests/appv22/test_tool_broker_registry.py
git commit -m "feat(appv22): add schema-aware tool broker"
```

---

## Task 4: Hermes Context Components

**Files:**
- Create: `appV2.2/appv22/context/budget.py`
- Create: `appV2.2/appv22/context/gateway_guard.py`
- Create: `appV2.2/appv22/context/summaries.py`
- Create: `appV2.2/appv22/context/compressor.py`
- Test: `tests/appv22/test_context_governance.py`

- [ ] **Step 1: Write failing tests**

```python
from appv22.context.compressor import AgentContextCompressor
from appv22.context.gateway_guard import GatewayContextGuard


def test_gateway_guard_prunes_verbose_tool_payloads_at_85_percent():
    messages = [{"role": "system", "content": "s"}, {"role": "tool", "tool_result_id": "toolres_old", "content": "x" * 9000}, {"role": "user", "content": "continue"}]

    guarded = GatewayContextGuard(max_chars=10_000, threshold=0.85).guard(messages)

    assert guarded[1]["content"] == "[pruned verbose tool result:toolres_old]"
    assert guarded[0]["content"] == "s"
    assert guarded[-1]["content"] == "continue"


def test_agent_compressor_emits_structured_summary():
    messages = [{"role": "system", "content": "s"}, {"role": "assistant", "content": "decision: observe"}, {"role": "tool", "tool_result_id": "toolres_1", "content": "x" * 5000}, {"role": "user", "content": "continue"}]

    compacted = AgentContextCompressor(max_chars=8_000, threshold=0.50).compress(messages, previous_summary={})

    assert compacted[0]["role"] == "system"
    assert compacted[1]["name"] == "context_summary"
    assert set(compacted[1]["summary"]) == {"goals", "decisions", "progress", "open_risks", "evidence_refs"}
```

- [ ] **Step 2: Run tests to verify failure**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_context_governance.py -q`

Expected: FAIL with missing context modules.

- [ ] **Step 3: Implement Hermes components**

```python
# appV2.2/appv22/context/budget.py
from __future__ import annotations

import json
from typing import Any


def estimate_chars(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, default=str))
```

```python
# appV2.2/appv22/context/gateway_guard.py
from __future__ import annotations

from copy import deepcopy

from appv22.context.budget import estimate_chars


class GatewayContextGuard:
    def __init__(self, *, max_chars: int, threshold: float = 0.85) -> None:
        self.max_chars = max_chars
        self.threshold = threshold

    def guard(self, messages: list[dict]) -> list[dict]:
        guarded = deepcopy(messages)
        if estimate_chars(guarded) <= int(self.max_chars * self.threshold):
            return guarded
        for message in guarded[1:-1]:
            if message.get("role") == "tool" and len(str(message.get("content", ""))) > 1000:
                message["content"] = f"[pruned verbose tool result:{message.get('tool_result_id', 'unknown')}]"
        return guarded
```

```python
# appV2.2/appv22/context/summaries.py
from __future__ import annotations


def structured_summary(messages: list[dict], previous_summary: dict) -> dict:
    return {
        "goals": previous_summary.get("goals") or [next((m.get("content", "") for m in messages if m.get("role") == "user"), "")],
        "decisions": [m.get("content", "") for m in messages if m.get("role") == "assistant" and "decision:" in str(m.get("content", "")).lower()],
        "progress": previous_summary.get("progress") or [],
        "open_risks": previous_summary.get("open_risks") or [],
        "evidence_refs": [m["tool_result_id"] for m in messages if m.get("role") == "tool" and m.get("tool_result_id")],
    }
```

```python
# appV2.2/appv22/context/compressor.py
from __future__ import annotations

from copy import deepcopy

from appv22.context.budget import estimate_chars
from appv22.context.summaries import structured_summary


class AgentContextCompressor:
    def __init__(self, *, max_chars: int, threshold: float = 0.50) -> None:
        self.max_chars = max_chars
        self.threshold = threshold

    def compress(self, messages: list[dict], *, previous_summary: dict) -> list[dict]:
        copied = deepcopy(messages)
        if estimate_chars(copied) <= int(self.max_chars * self.threshold):
            return copied
        head = copied[:1]
        tail = copied[-1:]
        middle = copied[1:-1]
        for message in middle:
            if message.get("role") == "tool" and len(str(message.get("content", ""))) > 1000:
                message["content"] = f"[pruned verbose tool result:{message.get('tool_result_id', 'unknown')}]"
        return [*head, {"role": "system", "name": "context_summary", "content": "Structured context summary injected.", "summary": structured_summary(middle, previous_summary)}, *tail]
```

- [ ] **Step 4: Run tests and commit**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_context_governance.py -q`

Expected: PASS.

Commit:

```bash
git add appV2.2/appv22/context tests/appv22/test_context_governance.py
git commit -m "feat(appv22): add hermes context governance"
```

---

## Task 5: Context Selector and Prompt Builder

**Files:**
- Create: `appV2.2/appv22/context/selector.py`
- Create: `appV2.2/appv22/context/prompt_builder.py`
- Test: `tests/appv22/test_prompt_context.py`

- [ ] **Step 1: Write failing tests**

```python
from appv22.context.prompt_builder import PromptBuilder
from appv22.context.selector import ContextSelector
from appv22.extensions.registry import ResolvedExtensions
from appv22.state.models import AgentState, RequestEnvelope


def test_prompt_uses_pre_turn_mode_and_hides_tools_in_plan():
    state = AgentState("sess", "run", RequestEnvelope("req", "clean this", "."), mode="PLAN")
    resolved = ResolvedExtensions(["demo"], [], ["demo.inspect"], ["demo.planner"], ["demo.policy"], ["demo.executor"], ["demo.verifier"], [])

    selected = ContextSelector().select(state, resolved, pre_turn_mode="PLAN")
    prompt = PromptBuilder().build(state, selected)

    assert prompt["agent"]["mode"] == "PLAN"
    assert prompt["selection"]["selected_tools"] == []
```

- [ ] **Step 2: Run test to verify failure**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_prompt_context.py -q`

Expected: FAIL with missing selector/prompt builder.

- [ ] **Step 3: Implement selector and prompt builder**

```python
# appV2.2/appv22/context/selector.py
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from appv22.extensions.registry import ResolvedExtensions
from appv22.state.models import AgentState

READ_TOOL_MODES = frozenset({"START", "THINK", "OBSERVE", "VERIFY"})


class ContextSelector:
    def select(self, state: AgentState, resolved: ResolvedExtensions, *, pre_turn_mode: str) -> dict[str, Any]:
        skill_cards = [card for card in resolved.skill_cards if pre_turn_mode in card.modes]
        selected_tools = resolved.tool_ids if pre_turn_mode in READ_TOOL_MODES else []
        return {
            "state": {"mode": pre_turn_mode, "runtime_plan": state.runtime_plan, "mutation_receipts": list(state.mutation_receipts), "verification_receipts": list(state.verification_receipts)},
            "skills": [asdict(card) for card in skill_cards],
            "tools": list(selected_tools),
            "world": {"world_refs": list(state.world_refs)},
            "selection": {"mode": pre_turn_mode, "selected_tools": list(selected_tools), "selected_skills": [card.skill_id for card in skill_cards]},
        }
```

```python
# appV2.2/appv22/context/prompt_builder.py
from __future__ import annotations

from typing import Any

from appv22.state.models import AgentState


class PromptBuilder:
    def build(self, state: AgentState, selected_context: dict[str, Any]) -> dict[str, Any]:
        mode = selected_context["selection"]["mode"]
        return {
            "system": {"identity": "AppV2.2 Pi-Hermes extension runtime"},
            "agent": {"mode": mode, "request": state.request.user_goal, "constraints": state.request.constraints},
            "state": selected_context["state"],
            "skills": selected_context["skills"],
            "tools": selected_context["tools"],
            "world": selected_context["world"],
            "selection": selected_context["selection"],
        }
```

- [ ] **Step 4: Run tests and commit**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_prompt_context.py -q`

Expected: PASS.

Commit:

```bash
git add appV2.2/appv22/context/selector.py appV2.2/appv22/context/prompt_builder.py tests/appv22/test_prompt_context.py
git commit -m "feat(appv22): add mode-aware prompt context"
```

---

## Task 6: File Management Extension Capabilities

**Files:**
- Create: `appV2.2/appv22/extensions/file_management/extension.py`
- Create: `appV2.2/appv22/extensions/file_management/skills.py`
- Create: `appV2.2/appv22/extensions/file_management/tools.py`
- Create: `appV2.2/appv22/extensions/file_management/planner.py`
- Create: `appV2.2/appv22/extensions/file_management/mutation_policy.py`
- Create: `appV2.2/appv22/extensions/file_management/mutation_executor.py`
- Create: `appV2.2/appv22/extensions/file_management/verifier.py`
- Create: `appV2.2/appv22/extensions/file_management/schemas.py`
- Test: `tests/appv22/test_file_management_extension.py`

- [ ] **Step 1: Write failing file-management tests**

```python
from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.extensions.registry import ExtensionRegistry
from appv22.runtime.capabilities import CapabilityRegistry
from appv22.state.models import AgentState, RequestEnvelope


def test_file_management_extension_registers_all_capabilities():
    extension = FileManagementExtension()
    registry = ExtensionRegistry()
    capabilities = CapabilityRegistry()
    registry.register(extension)
    extension.register_capabilities(capabilities)
    state = AgentState("sess", "run", RequestEnvelope("req", "tidy this workspace mess", "."))

    resolved = registry.resolve_active(state)

    assert resolved.extension_ids == ["file_management"]
    assert resolved.planner_ids == ["file_management.cleanup_planner"]
    assert resolved.mutation_policy_ids == ["file_management.safe_file_moves"]
    assert resolved.mutation_executor_ids == ["file_management.file_mutation_executor"]
    assert resolved.verifier_ids == ["file_management.manifest_verifier"]
    assert capabilities.planner("file_management.cleanup_planner")
    assert capabilities.mutation_policy("file_management.safe_file_moves")
    assert capabilities.mutation_executor("file_management.file_mutation_executor")
    assert capabilities.verifier("file_management.manifest_verifier")


def test_file_management_skill_activation_handles_vague_prompts():
    extension = FileManagementExtension()
    state = AgentState("sess", "run", RequestEnvelope("req", "make this workspace sane and keep a record", "."))

    assert extension.skill_cards()[0].activates_for(state) is True
```

- [ ] **Step 2: Run tests to verify failure**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_file_management_extension.py -q`

Expected: FAIL with missing file-management extension.

- [ ] **Step 3: Implement skill and extension registration**

```python
# appV2.2/appv22/extensions/file_management/skills.py
from __future__ import annotations

from appv22.extensions.base import SkillCard

FILE_MANAGEMENT_SKILL = SkillCard(
    skill_id="file_management.cleanup",
    extension_id="file_management",
    triggers=("clean", "cleanup", "organize", "mess", "tidy", "workspace", "clutter", "sane"),
    modes=("START", "THINK", "OBSERVE", "PLAN", "ACT", "VERIFY"),
    summary="Safely organize workspace files and record moves, held paths, and collisions.",
    planner_id="file_management.cleanup_planner",
    mutation_policy_id="file_management.safe_file_moves",
    mutation_executor_id="file_management.file_mutation_executor",
    verifier_id="file_management.manifest_verifier",
    tool_ids=("file_management.repo_snapshot", "file_management.read_file"),
    artifact_schema_ids=("file_management.workspace_manifest",),
)
```

```python
# appV2.2/appv22/extensions/file_management/extension.py
from __future__ import annotations

from appv22.extensions.file_management.mutation_executor import FileMutationExecutor
from appv22.extensions.file_management.mutation_policy import FileMoveMutationPolicy
from appv22.extensions.file_management.planner import FileCleanupPlanner
from appv22.extensions.file_management.schemas import WORKSPACE_MANIFEST_SCHEMA
from appv22.extensions.file_management.skills import FILE_MANAGEMENT_SKILL
from appv22.extensions.file_management.tools import register_file_management_tools
from appv22.extensions.file_management.verifier import WorkspaceManifestVerifier


class FileManagementExtension:
    extension_id = "file_management"

    def skill_cards(self):
        return [FILE_MANAGEMENT_SKILL]

    def register_tools(self, registry) -> None:
        register_file_management_tools(registry)

    def register_capabilities(self, capabilities) -> None:
        capabilities.register_planner("file_management.cleanup_planner", FileCleanupPlanner())
        capabilities.register_mutation_policy("file_management.safe_file_moves", FileMoveMutationPolicy())
        capabilities.register_mutation_executor("file_management.file_mutation_executor", FileMutationExecutor())
        capabilities.register_verifier("file_management.manifest_verifier", WorkspaceManifestVerifier())
        capabilities.register_artifact_schema("file_management.workspace_manifest", WORKSPACE_MANIFEST_SCHEMA)
```

- [ ] **Step 4: Implement file-management internals behind extension boundary**

```python
# appV2.2/appv22/extensions/file_management/planner.py
from __future__ import annotations

import json
from pathlib import Path


class FileCleanupPlanner:
    def plan(self, state) -> dict:
        snapshot = state.world_refs["world://repo_snapshot/latest"]["payload"]
        files = [path for path in snapshot.get("files", []) if isinstance(path, str)]
        existing = set(files)
        destinations: dict[str, str] = {}
        moves: list[dict[str, str]] = []
        held: list[str] = []
        collisions: list[dict[str, str]] = []
        operations: list[dict] = []
        for source in files:
            if _preserve(source):
                continue
            destination = _destination(source)
            if destination is None:
                continue
            if destination in existing or destination in destinations:
                held.append(source)
                collisions.append({"source": source, "destination": destination, "conflicts_with": destinations.get(destination, destination)})
                continue
            destinations[destination] = source
            moves.append({"source": source, "destination": destination})
            operations.append({"action": "move", "source": source, "destination": destination})
        manifest = {"generated_by": "appv22", "moves": moves, "held": held, "collisions": collisions}
        operations.append({"action": "write", "path": "docs/workspace_manifest.json", "content": json.dumps(manifest, indent=2, sort_keys=True)})
        return {"planner_id": "file_management.cleanup_planner", "mutation_policy_id": "file_management.safe_file_moves", "mutation_executor_id": "file_management.file_mutation_executor", "verifier_id": "file_management.manifest_verifier", "mutation_intent": {"operation_batch_id": "workspace_cleanup", "operations": operations}, "verification_intent": {"manifest_path": "docs/workspace_manifest.json", "moves": moves, "held": held}}


def _destination(source: str) -> str | None:
    name = Path(source).name
    suffix = Path(source).suffix.lower()
    if suffix == ".md":
        return f"docs/{name}"
    if suffix in {".log", ".json"}:
        return f"artifacts/logs/{name}"
    return None


def _preserve(source: str) -> bool:
    if source == "README.md" or source.startswith(("tests/", "src/", "assets/", "secrets/", "docs/")):
        return True
    return Path(source).name.lower().startswith(("keep", "do_not_move", "old_blob"))
```

```python
# appV2.2/appv22/extensions/file_management/mutation_policy.py
from __future__ import annotations

from pathlib import Path


class FileMoveMutationPolicy:
    def validate(self, operations: list[dict], *, root_path) -> list[str]:
        errors: list[str] = []
        root = Path(root_path).resolve()
        for operation in operations:
            action = operation.get("action")
            if action == "move":
                source = str(operation.get("source", ""))
                destination = str(operation.get("destination", ""))
                if _outside(root, source) or _outside(root, destination):
                    errors.append(f"path_outside_root:{source}->{destination}")
                if _preserve(source):
                    errors.append(f"protected_source_path:{source}")
                if (root / destination).exists():
                    errors.append(f"destination_exists:{destination}")
            elif action == "write":
                if operation.get("path") != "docs/workspace_manifest.json":
                    errors.append(f"unsupported_write_path:{operation.get('path')}")
            else:
                errors.append(f"unsupported_operation:{action}")
        return errors


def _outside(root: Path, relative: str) -> bool:
    try:
        (root / relative).resolve().relative_to(root)
        return False
    except ValueError:
        return True


def _preserve(source: str) -> bool:
    if source == "README.md" or source.startswith(("tests/", "src/", "assets/", "secrets/", "docs/")):
        return True
    return Path(source).name.lower().startswith(("keep", "do_not_move", "old_blob"))
```

```python
# appV2.2/appv22/extensions/file_management/mutation_executor.py
from __future__ import annotations

import json
import shutil
from pathlib import Path


class FileMutationExecutor:
    def apply(self, operations: list[dict], *, root_path) -> dict:
        root = Path(root_path)
        touched: list[str] = []
        errors: list[str] = []
        for operation in operations:
            if operation["action"] == "move":
                source = root / operation["source"]
                destination = root / operation["destination"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                touched.extend([operation["source"], operation["destination"]])
            elif operation["action"] == "write":
                path = root / operation["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                content = operation["content"] if isinstance(operation["content"], str) else json.dumps(operation["content"], indent=2, sort_keys=True)
                path.write_text(content, encoding="utf-8")
                touched.append(operation["path"])
        return {"status": "applied" if not errors else "failed", "touched_paths": sorted(set(touched)), "errors": errors}
```

```python
# appV2.2/appv22/extensions/file_management/tools.py
from __future__ import annotations

from pathlib import Path

from appv22.tools.definitions import ToolDefinition


def register_file_management_tools(registry) -> None:
    registry.register(ToolDefinition("file_management.repo_snapshot", "observe", "low", {}, {}, "runtime_observed", "Return files/directories."), repo_snapshot)


def repo_snapshot(_args: dict, context: dict) -> dict:
    root = Path(context["root_path"])
    return {"status": "completed", "files": sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()), "directories": sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_dir())}
```

```python
# appV2.2/appv22/extensions/file_management/verifier.py
from __future__ import annotations

import json
from pathlib import Path


class WorkspaceManifestVerifier:
    def verify(self, *, root_path, verification_intent: dict) -> dict:
        manifest_path = Path(root_path) / verification_intent.get("manifest_path", "docs/workspace_manifest.json")
        exists = manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if exists else {}
        checks = [{"name": "manifest_exists", "passed": exists}] + [{"name": f"manifest_has_{key}", "passed": key in manifest} for key in ("moves", "held", "collisions")]
        return {"status": "passed" if all(check["passed"] for check in checks) else "failed", "checks": checks, "manifest": manifest}
```

```python
# appV2.2/appv22/extensions/file_management/schemas.py
WORKSPACE_MANIFEST_SCHEMA = {"schema_id": "file_management.workspace_manifest", "required": ["generated_by", "moves", "held", "collisions"]}
```

- [ ] **Step 5: Run tests and commit**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_file_management_extension.py -q`

Expected: PASS.

Commit:

```bash
git add appV2.2/appv22/extensions/file_management tests/appv22/test_file_management_extension.py
git commit -m "feat(appv22): add file management extension capabilities"
```

---

## Task 7: Pi-Style Agent Loop With Capability Resolution

**Files:**
- Create: `appV2.2/appv22/runtime/reducer.py`
- Create: `appV2.2/appv22/runtime/services.py`
- Create: `appV2.2/appv22/runtime/agent_loop.py`
- Modify: `appV2.2/appv22/__init__.py`
- Test: `tests/appv22/test_agent_loop_extension_runtime.py`
- Test: `tests/appv22/test_architecture_boundaries.py`

- [ ] **Step 1: Write failing end-to-end extension loop test**

```python
from appv22 import AppV22AgentRuntime
from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.providers.deterministic import DeterministicAppV22Provider
from appv22.runtime.services import create_appv22_services


def test_agent_loop_uses_capability_registry_without_file_imports(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text("a", encoding="utf-8")
    services = create_appv22_services(root_path=tmp_path, provider=DeterministicAppV22Provider(), extensions=[FileManagementExtension()])

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=8).run("make this workspace sane and keep a record")

    assert result["status"] == "completed"
    assert (tmp_path / "docs" / "a.md").is_file()
    assert result["mutation_receipts"]
    assert result["verification_receipts"]
```

- [ ] **Step 2: Run test to verify failure**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_agent_loop_extension_runtime.py::test_agent_loop_uses_capability_registry_without_file_imports -q`

Expected: FAIL with missing runtime loop/provider.

- [ ] **Step 3: Implement services and reducer**

```python
# appV2.2/appv22/runtime/services.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from appv22.context.compressor import AgentContextCompressor
from appv22.context.gateway_guard import GatewayContextGuard
from appv22.context.prompt_builder import PromptBuilder
from appv22.context.selector import ContextSelector
from appv22.extensions.registry import ExtensionRegistry
from appv22.runtime.capabilities import CapabilityRegistry
from appv22.tools.broker import ToolBroker
from appv22.tools.registry import ToolRegistry


@dataclass
class AppV22Services:
    root_path: Path
    provider: object
    extension_registry: ExtensionRegistry
    capability_registry: CapabilityRegistry
    tool_registry: ToolRegistry
    broker: ToolBroker
    context_selector: ContextSelector
    prompt_builder: PromptBuilder
    gateway_guard: GatewayContextGuard
    compressor: AgentContextCompressor


def create_appv22_services(*, root_path, provider, extensions) -> AppV22Services:
    root = Path(root_path)
    extension_registry = ExtensionRegistry()
    capability_registry = CapabilityRegistry()
    tool_registry = ToolRegistry()
    for extension in extensions:
        extension_registry.register(extension)
        extension.register_capabilities(capability_registry)
        register_tools = getattr(extension, "register_tools", None)
        if callable(register_tools):
            register_tools(tool_registry)
    return AppV22Services(root, provider, extension_registry, capability_registry, tool_registry, ToolBroker(registry=tool_registry, root_path=root), ContextSelector(), PromptBuilder(), GatewayContextGuard(max_chars=120_000), AgentContextCompressor(max_chars=120_000))
```

```python
# appV2.2/appv22/runtime/reducer.py
from __future__ import annotations


def apply_event(state, event) -> None:
    payload = event.payload
    if event.event_type == "ModeChanged":
        state.mode = payload["mode"]
    elif event.event_type == "WorldRefAdded":
        state.world_refs[payload["ref_id"]] = payload
    elif event.event_type == "ToolCallCompleted":
        state.tool_results[payload["tool_result_id"]] = payload
    elif event.event_type == "PlanAccepted":
        state.runtime_plan = payload
    elif event.event_type == "MutationLeaseIssued":
        state.mutation_leases[payload["lease_id"]] = payload
    elif event.event_type == "MutationApplied":
        state.mutation_receipts[payload["receipt_id"]] = payload
    elif event.event_type == "VerificationRecorded":
        state.verification_receipts[payload["verification_id"]] = payload
    elif event.event_type == "RunCompleted":
        state.terminal = True
        state.mode = "FINALIZE"
        state.result = payload
    elif event.event_type == "RunFailed":
        state.terminal = True
        state.mode = "FAILED"
        state.result = payload
```

- [ ] **Step 4: Implement domain-free agent loop**

```python
# appV2.2/appv22/runtime/agent_loop.py
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from appv22.runtime.reducer import apply_event
from appv22.runtime.services import AppV22Services
from appv22.state.events import RuntimeEvent
from appv22.state.models import AgentState, RequestEnvelope


class AppV22AgentRuntime:
    def __init__(self, *, root_path: str | Path, services: AppV22Services, max_turns: int = 12) -> None:
        self.root_path = Path(root_path)
        self.services = services
        self.max_turns = max_turns
        self.events: list[RuntimeEvent] = []

    def run(self, user_goal: str) -> dict:
        state = AgentState(f"sess_{uuid4().hex}", f"run_{uuid4().hex}", RequestEnvelope(f"req_{uuid4().hex}", user_goal, str(self.root_path)))
        for turn_index in range(self.max_turns):
            resolved = self.services.extension_registry.resolve_active(state)
            state.active_extension_ids = resolved.extension_ids
            state.active_skill_ids = [card.skill_id for card in resolved.skill_cards]
            selected = self.services.context_selector.select(state, resolved, pre_turn_mode=state.mode)
            prompt = self.services.prompt_builder.build(state, selected)
            prompt_messages = self.services.gateway_guard.guard([{"role": "system", "content": str(prompt["system"])}, {"role": "user", "content": state.request.user_goal}])
            compressed_messages = self.services.compressor.compress(prompt_messages, previous_summary=state.context_summary)
            prompt["messages"] = compressed_messages
            decision = self.services.provider.decide(prompt)
            self._apply(state, RuntimeEvent("DecisionProposed", {"turn_index": turn_index, **decision.to_dict()}))
            self._route(state, decision, resolved)
            if state.terminal:
                return {**state.result, "events": [event.to_dict() for event in self.events]}
        self._apply(state, RuntimeEvent("RunFailed", {"status": "failed", "reason": "max_turns_exceeded"}))
        return {**state.result, "events": [event.to_dict() for event in self.events]}

    def _route(self, state, decision, resolved) -> None:
        if decision.kind == "tool_call":
            self._apply(state, RuntimeEvent("ModeChanged", {"mode": "OBSERVE"}))
            result = self.services.broker.execute(decision.payload["tool_id"], decision.payload.get("arguments", {}), active_tool_ids=resolved.tool_ids)
            self._apply(state, RuntimeEvent("ToolCallCompleted", result))
            if result["status"] == "completed" and decision.payload["tool_id"].endswith("repo_snapshot"):
                self._apply(state, RuntimeEvent("WorldRefAdded", {"ref_id": "world://repo_snapshot/latest", "kind": "repo_snapshot", "payload": result["payload"], "summary": "repo snapshot"}))
            return
        if decision.kind == "plan":
            self._apply(state, RuntimeEvent("ModeChanged", {"mode": "PLAN"}))
            planner = self.services.capability_registry.planner(resolved.planner_ids[0])
            self._apply(state, RuntimeEvent("PlanAccepted", planner.plan(state)))
            return
        if decision.kind == "mutation_intent":
            self._apply(state, RuntimeEvent("ModeChanged", {"mode": "ACT"}))
            policy = self.services.capability_registry.mutation_policy(state.runtime_plan["mutation_policy_id"])
            executor = self.services.capability_registry.mutation_executor(state.runtime_plan["mutation_executor_id"])
            operations = decision.payload["operations"]
            errors = policy.validate(operations, root_path=self.root_path)
            if errors:
                self._apply(state, RuntimeEvent("RunFailed", {"status": "failed", "reason": "mutation_denied", "errors": errors}))
                return
            lease_id = f"lease_{uuid4().hex}"
            self._apply(state, RuntimeEvent("MutationLeaseIssued", {"lease_id": lease_id, "operation_batch_id": decision.payload["operation_batch_id"], "allowed_operations": operations}))
            applied = executor.apply(operations, root_path=self.root_path)
            self._apply(state, RuntimeEvent("MutationApplied", {"receipt_id": f"mut_{decision.payload['operation_batch_id']}", "lease_id": lease_id, "operations": operations, **applied}))
            return
        if decision.kind == "finalize":
            verifier = self.services.capability_registry.verifier(state.runtime_plan["verifier_id"])
            verification = verifier.verify(root_path=self.root_path, verification_intent=state.runtime_plan["verification_intent"])
            verification_id = f"verify_{uuid4().hex}"
            self._apply(state, RuntimeEvent("VerificationRecorded", {"verification_id": verification_id, **verification}))
            if verification["status"] != "passed":
                self._apply(state, RuntimeEvent("RunFailed", {"status": "failed", "reason": "verification_failed"}))
                return
            self._apply(state, RuntimeEvent("RunCompleted", {"status": "completed", "mutation_receipts": list(state.mutation_receipts), "verification_receipts": list(state.verification_receipts)}))

    def _apply(self, state, event: RuntimeEvent) -> None:
        self.events.append(event)
        apply_event(state, event)
```

```python
# appV2.2/appv22/__init__.py
from appv22.runtime.agent_loop import AppV22AgentRuntime

__all__ = ["AppV22AgentRuntime"]
```

- [ ] **Step 5: Run tests and commit**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_agent_loop_extension_runtime.py tests/appv22/test_architecture_boundaries.py -q`

Expected: PASS, including no `extensions.file_management` imports in runtime core.

Commit:

```bash
git add appV2.2/appv22/runtime appV2.2/appv22/__init__.py tests/appv22/test_agent_loop_extension_runtime.py tests/appv22/test_architecture_boundaries.py
git commit -m "feat(appv22): add pi-style capability-driven agent loop"
```

---

## Task 8: Providers and AppV2 Env Decision Adapter

**Files:**
- Create: `appV2.2/appv22/providers/deterministic.py`
- Create: `appV2.2/appv22/providers/appv2_env.py`
- Test: `tests/appv22/test_provider_adapters.py`

- [ ] **Step 1: Write failing provider adapter tests**

```python
from appv22.providers.appv2_env import normalize_appv22_decision_payload
from appv22.providers.deterministic import DeterministicAppV22Provider


def test_deterministic_provider_uses_appv22_tool_id_shape():
    decision = DeterministicAppV22Provider().decide({"world": {"world_refs": []}, "state": {"runtime_plan": {}, "mutation_receipts": []}})

    assert decision.kind == "tool_call"
    assert decision.payload == {"tool_id": "file_management.repo_snapshot", "arguments": {}}


def test_appv2_env_adapter_normalizes_tool_name_to_tool_id():
    payload = {"kind": "tool_call", "payload": {"tool_name": "repo_snapshot", "arguments": {}}}

    normalized = normalize_appv22_decision_payload(payload)

    assert normalized["payload"] == {"tool_id": "file_management.repo_snapshot", "arguments": {}}
```

- [ ] **Step 2: Run tests to verify failure**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_provider_adapters.py -q`

Expected: FAIL with missing providers.

- [ ] **Step 3: Implement providers**

```python
# appV2.2/appv22/providers/deterministic.py
from __future__ import annotations

from appv22.runtime.decisions import RuntimeDecision


class DeterministicAppV22Provider:
    provider_id = "deterministic-appv22"

    def decide(self, prompt: dict) -> RuntimeDecision:
        if not prompt["world"]["world_refs"]:
            return RuntimeDecision("tool_call", "observe first", {"tool_id": "file_management.repo_snapshot", "arguments": {}})
        if not prompt["state"]["runtime_plan"]:
            return RuntimeDecision("plan", "plan from observed snapshot", evidence_refs=["world://repo_snapshot/latest"])
        if not prompt["state"]["mutation_receipts"]:
            return RuntimeDecision("mutation_intent", "apply extension plan", prompt["state"]["runtime_plan"]["mutation_intent"], ["plan://accepted/latest"])
        return RuntimeDecision("finalize", "verify and finish")
```

```python
# appV2.2/appv22/providers/appv2_env.py
from __future__ import annotations

from appv21.providers.appv2_env import create_appv21_provider_from_appv2_env

TOOL_NAME_MAP = {"repo_snapshot": "file_management.repo_snapshot", "read_file": "file_management.read_file"}


def normalize_appv22_decision_payload(raw: dict) -> dict:
    normalized = dict(raw)
    payload = dict(normalized.get("payload") or {})
    if normalized.get("kind") == "tool_call":
        tool_id = payload.get("tool_id") or TOOL_NAME_MAP.get(payload.get("tool_name"), payload.get("tool_name"))
        normalized["payload"] = {"tool_id": tool_id, "arguments": payload.get("arguments") or payload.get("params") or {}}
    return normalized


def create_appv22_provider_from_appv2_env(*, dotenv_path: str):
    provider = create_appv21_provider_from_appv2_env(dotenv_path=dotenv_path)
    original_decide = provider.decide

    def decide(prompt: dict):
        decision = original_decide(prompt)
        payload = normalize_appv22_decision_payload(decision.to_dict())
        from appv22.runtime.decisions import RuntimeDecision
        return RuntimeDecision(kind=payload["kind"], reason=payload["reason"], payload=payload.get("payload") or {}, evidence_refs=payload.get("evidence_refs") or [], decision_id=payload["decision_id"])

    provider.decide = decide
    return provider
```

- [ ] **Step 4: Run tests and commit**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_provider_adapters.py -q`

Expected: PASS.

Commit:

```bash
git add appV2.2/appv22/providers tests/appv22/test_provider_adapters.py
git commit -m "feat(appv22): add appv2 provider decision adapter"
```

---

## Task 9: Complex Vague-Prompt Probe

**Files:**
- Create: `scripts/live_appv22_complex_vague_file_management_probe.py`
- Test: `tests/appv22/test_live_probe_report.py`

- [ ] **Step 1: Write failing report test**

```python
from scripts.live_appv22_complex_vague_file_management_probe import build_report


def test_probe_report_contains_full_matrix(tmp_path):
    result = {"status": "completed", "events": [{"event_type": "DecisionProposed", "payload": {"kind": "tool_call"}}, {"event_type": "ToolCallCompleted", "payload": {"tool_id": "file_management.repo_snapshot"}}, {"event_type": "MutationApplied", "payload": {"receipt_id": "mut_workspace_cleanup"}}, {"event_type": "VerificationRecorded", "payload": {"verification_id": "verify_1"}}]}

    report = build_report(repo=tmp_path, result=result, provider=None, prompt="Can you clean this mess up safely and keep a record?")

    assert report["status"] == "completed"
    assert report["user_prompt"] == "Can you clean this mess up safely and keep a record?"
    assert report["totals"]["decisions"] == 1
    assert report["totals"]["tool_calls"] == 1
    assert report["totals"]["mutation_receipts"] == 1
    assert report["totals"]["verification_receipts"] == 1
```

- [ ] **Step 2: Run test to verify failure**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_live_probe_report.py -q`

Expected: FAIL with missing probe script.

- [ ] **Step 3: Implement probe script**

```python
# scripts/live_appv22_complex_vague_file_management_probe.py
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "appV2.2"))

from appv22 import AppV22AgentRuntime
from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.providers.appv2_env import create_appv22_provider_from_appv2_env
from appv22.providers.deterministic import DeterministicAppV22Provider
from appv22.runtime.services import create_appv22_services


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["deterministic", "appv2-env"], default="deterministic")
    parser.add_argument("--dotenv", default=".env")
    parser.add_argument("--prompt", default="Can you clean this mess up safely and keep a record?")
    args = parser.parse_args()
    repo = seed_repo(ROOT / "live_appv22_complex_vague_file_management_repo")
    provider = DeterministicAppV22Provider() if args.provider == "deterministic" else create_appv22_provider_from_appv2_env(dotenv_path=args.dotenv)
    services = create_appv22_services(root_path=repo, provider=provider, extensions=[FileManagementExtension()])
    result = AppV22AgentRuntime(root_path=repo, services=services, max_turns=12).run(args.prompt)
    report = build_report(repo=repo, result=result, provider=provider, prompt=args.prompt)
    out = ROOT / "plan" / "live-appv22-complex-vague-file-management-probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": report["status"], "totals": report["totals"], "costs": report["costs"], "output_path": str(out)}, sort_keys=True))
    return 0 if report["status"] == "completed" else 1


def seed_repo(repo: Path) -> Path:
    if repo.exists():
        shutil.rmtree(repo)
    files = {"README.md": "# Probe\n", "notes/team/standup.md": "standup\n", "notes/team/keep_decisions.md": "keep\n", "projects/alpha/spec.md": "alpha\n", "projects/beta/spec.md": "beta collision\n", "tmp/session/run.log": "log\n", "assets/logo.svg": "<svg></svg>\n", "src/app.py": "print('hi')\n", "tests/test_probe.py": "def test_probe(): assert True\n"}
    for path, content in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return repo


def build_report(*, repo: Path, result: dict[str, Any], provider: Any, prompt: str) -> dict[str, Any]:
    events = result.get("events", [])
    event_types = [event["event_type"] for event in events]
    costs = getattr(getattr(provider, "client", None), "usage_snapshot", lambda: {"model_calls": 0, "total_tokens": 0, "cost": 0.0})()
    return {"status": result.get("status"), "reason": result.get("reason"), "user_prompt": prompt, "provider": getattr(provider, "provider_id", "deterministic"), "totals": {"events": len(events), "decisions": event_types.count("DecisionProposed"), "tool_calls": event_types.count("ToolCallCompleted") + event_types.count("ToolCallDenied"), "mutation_receipts": event_types.count("MutationApplied"), "verification_receipts": event_types.count("VerificationRecorded")}, "costs": costs, "event_order": event_types, "files": sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*") if path.is_file())}


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests and deterministic probe**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_live_probe_report.py -q`

Expected: PASS.

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run python scripts/live_appv22_complex_vague_file_management_probe.py --provider deterministic`

Expected: exit `0`, status `completed`.

Commit:

```bash
git add scripts/live_appv22_complex_vague_file_management_probe.py tests/appv22/test_live_probe_report.py
git commit -m "test(appv22): add complex vague prompt probe"
```

---

## Task 10: Full QA and Live AppV2 Env Probe

**Files:**
- Modify only if verification exposes failures.

- [ ] **Step 1: Run unit suite**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22 -q`

Expected: all tests pass.

- [ ] **Step 2: Run compile check**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run python -m compileall -q appV2.2 tests/appv22 scripts/live_appv22_complex_vague_file_management_probe.py`

Expected: exit `0`.

- [ ] **Step 3: Run architecture boundary check explicitly**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_architecture_boundaries.py -q`

Expected: PASS and no runtime imports from `extensions.file_management`.

- [ ] **Step 4: Run deterministic probe**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run python scripts/live_appv22_complex_vague_file_management_probe.py --provider deterministic`

Expected: status `completed`, at least one mutation receipt, at least one verification receipt.

- [ ] **Step 5: Run live appv2-env probe**

Run: `UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run python scripts/live_appv22_complex_vague_file_management_probe.py --provider appv2-env --dotenv /Users/htooayelwin/lewis/allthebest/.env`

Expected:
- status `completed`
- vague prompt activates file-management skill
- runtime core remains extension-agnostic
- no protected source paths moved
- manifest exists and records moves, held paths, and collisions
- cost matrix includes model calls and token totals if provider exposes usage

- [ ] **Step 6: Report final matrix**

Final answer must include:

```text
status
model_calls
total_tokens
estimated_cost_usd if available
tool_calls
mutation_receipts
verification_receipts
manifest moves/held/collisions
report path
```

---

## Corrected Plan QA Checklist

- **No file-management imports in runtime core:** enforced by `tests/appv22/test_architecture_boundaries.py`.
- **No mutation execution in runtime core:** runtime calls `capability_registry.mutation_executor(...).apply(...)`.
- **No planner in tool broker:** planner lives in capability registry and is selected by active skill.
- **Provider compatibility:** AppV2 `.env` decisions are normalized from `tool_name` to AppV2.2 `tool_id`.
- **Hermes integration:** gateway guard and compressor are called in `AppV22AgentRuntime.run()` before provider call.
- **Schema validation:** tool broker enforces required args from registered tool definitions.
- **Vague prompt support:** file-management skill includes `tidy`, `workspace`, `clutter`, and `sane` triggers.
- **Destination safety:** file-management mutation policy checks outside-root, protected source, destination existence, unsupported writes, and unsupported operations.
- **Second extension property:** architecture test proves runtime does not depend on file-management imports.
