# AppV2 Executable Phase Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor AppV2 so workers consume runtime-compiled executable phase contracts instead of fragile raw planner artifact topology.

**Architecture:** Add a deterministic artifact topology compiler that turns raw `PhasePlan` artifact contracts into trusted `ExecutablePhaseContract` objects with explicit input ports, output ports, evidence rules, and completion obligations. Keep planner output as semantic intent only; runtime compilers own executable artifact ports, tool policy, mutation policy, verification policy, and discover-phase evidence synthesis.

**Tech Stack:** Python 3.13, Pydantic schemas, pytest, existing AppV2 runtime modules under `appV2/`, live probe runner `scripts/live_appv2_runtime_probe.py`.

---

## Design Principles

1. The worker must not rely on raw planner-owned artifact topology.
2. `kind="input"` must not imply runtime availability unless the artifact is a known runtime input.
3. Tool observations are evidence records. Phase outputs are derived artifacts at phase boundaries.
4. Runtime compilers may repair deterministic planner omissions; ambiguous topology must block before worker execution.
5. The trusted worker interface is `ExecutablePhaseContract`, not `PhaseStep` alone.
6. The existing `PhasePlan` remains the external and persistence schema to avoid a large migration.

## File Structure

- Create: `appV2/artifact_topology.py`
  - Owns artifact port compilation, runtime-input classification, producer repair, evidence-rule assignment, and audit metadata.
- Create: `appV2/executable_contracts.py`
  - Defines focused Pydantic models: `ArtifactPort`, `EvidenceRule`, `CompletionObligation`, and `ExecutablePhaseContract`.
- Modify: `appV2/policy_compiler.py`
  - Calls artifact topology compilation before mutation and verification policy compilation.
- Modify: `appV2/validator.py`
  - Adds validation for untrusted runtime inputs and impossible artifact topology after compilation.
- Modify: `appV2/worker/context.py`
  - Builds phase frames from compiled completion obligations and output ports, preserving compatibility with existing `PhaseStep.output_artifacts`.
- Modify: `appV2/worker/agent_loop.py`
  - Uses compiled output obligations for DISCOVER synthesis and duplicate-tool recovery.
- Modify: `appV2/planner/prompt_chain.py`
  - Keeps planner repair, but validates against compiled topology metadata rather than trusting raw artifact kinds.
- Test: `tests/test_appv2_artifact_topology.py`
  - Unit tests for topology compilation and impossible graph detection.
- Test: `tests/test_appv2_worker_loop.py`
  - Regression tests for DISCOVER evidence synthesis when raw planner output omitted output artifacts.
- Test: `tests/test_appv2_policy_compiler.py`
  - Integration tests that policy compilation includes artifact topology audit metadata.
- Docs: `docs/appv2-runtime.md`
  - Documents planner intent vs runtime executable contract boundary.
- Docs: `docs/appv2-policy-compiler.md`
  - Adds artifact topology compiler as a sibling compiler.

---

### Task 1: Add executable contract schema models

**Files:**
- Create: `appV2/executable_contracts.py`
- Test: `tests/test_appv2_artifact_topology.py`

- [ ] **Step 1: Write failing schema tests**

Add `tests/test_appv2_artifact_topology.py`:

```python
from appV2.executable_contracts import ArtifactPort, CompletionObligation, EvidenceRule, ExecutablePhaseContract


def test_executable_phase_contract_serializes_artifact_ports() -> None:
    contract = ExecutablePhaseContract(
        phase_id="discover_01",
        phase="DISCOVER",
        input_ports=[],
        output_ports=[
            ArtifactPort(
                id="repo_file_inventory",
                direction="phase_output",
                required=True,
                producer_phase_id="discover_01",
                consumer_phase_ids=["analyze_01"],
                evidence_rule=EvidenceRule(any_tool=["repo_snapshot", "list_dir"]),
                synthesis_strategy="repo_inventory_from_discovery_evidence",
            )
        ],
        completion_obligations=[
            CompletionObligation(
                artifact_id="repo_file_inventory",
                required=True,
                evidence_rule=EvidenceRule(any_tool=["repo_snapshot", "list_dir"]),
                synthesis_strategy="repo_inventory_from_discovery_evidence",
            )
        ],
    )

    payload = contract.model_dump(mode="json")

    assert payload["phase_id"] == "discover_01"
    assert payload["output_ports"][0]["id"] == "repo_file_inventory"
    assert payload["output_ports"][0]["direction"] == "phase_output"
    assert payload["completion_obligations"][0]["evidence_rule"]["any_tool"] == ["repo_snapshot", "list_dir"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_appv2_artifact_topology.py::test_executable_phase_contract_serializes_artifact_ports -q
```

Expected: fail with `ModuleNotFoundError: No module named 'appV2.executable_contracts'`.

- [ ] **Step 3: Implement schema models**

Create `appV2/executable_contracts.py`:

```python
"""Runtime-compiled executable contracts for AppV2 phases."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from appV2.schemas import PhaseName


ArtifactPortDirection = Literal["runtime_input", "phase_input", "phase_output"]


class EvidenceRule(BaseModel):
    """Tool evidence that can satisfy a compiled completion obligation."""

    any_tool: list[str] = Field(default_factory=list)
    all_tools: list[str] = Field(default_factory=list)
    trust_levels: list[str] = Field(default_factory=lambda: ["tool_observed", "runtime_verified"])


class ArtifactPort(BaseModel):
    """A runtime-owned artifact port used by executable phases."""

    id: str
    direction: ArtifactPortDirection
    required: bool = True
    producer_phase_id: str | None = None
    consumer_phase_ids: list[str] = Field(default_factory=list)
    evidence_rule: EvidenceRule | None = None
    synthesis_strategy: str | None = None


class CompletionObligation(BaseModel):
    """A phase boundary output that must be produced or synthesized."""

    artifact_id: str
    required: bool = True
    evidence_rule: EvidenceRule | None = None
    synthesis_strategy: str | None = None


class ExecutablePhaseContract(BaseModel):
    """Trusted worker-facing phase contract compiled by runtime code."""

    phase_id: str
    phase: PhaseName
    input_ports: list[ArtifactPort] = Field(default_factory=list)
    output_ports: list[ArtifactPort] = Field(default_factory=list)
    completion_obligations: list[CompletionObligation] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv run pytest tests/test_appv2_artifact_topology.py::test_executable_phase_contract_serializes_artifact_ports -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add appV2/executable_contracts.py tests/test_appv2_artifact_topology.py
git commit -m "feat(appv2): add executable phase contract schemas"
```

---

### Task 2: Implement artifact topology compiler for the live-probe failure shape

**Files:**
- Create: `appV2/artifact_topology.py`
- Modify: `tests/test_appv2_artifact_topology.py`

- [ ] **Step 1: Add failing topology repair test**

Append to `tests/test_appv2_artifact_topology.py`:

```python
from appV2.artifact_topology import compile_artifact_topology
from appV2.schemas import PhasePlan


def test_topology_compiler_assigns_downstream_repo_inventory_to_discover() -> None:
    plan = PhasePlan.model_validate(
        {
            "plan_id": "v2_plan_001",
            "request_id": "v2_req_001",
            "objective": "Clean workspace.",
            "strategy": "discover_analyze_mutate_verify_finalize",
            "phases": [
                {
                    "phase_id": "discover_01",
                    "phase": "DISCOVER",
                    "goal": "Inspect repository.",
                    "instructions": ["Gather repo inventory."],
                    "input_artifacts": [],
                    "output_artifacts": [],
                    "allowed_tool_groups": ["repo_read"],
                },
                {
                    "phase_id": "analyze_01",
                    "phase": "ANALYZE",
                    "goal": "Plan reorganization.",
                    "instructions": ["Use repo inventory."],
                    "input_artifacts": ["repo_file_inventory"],
                    "output_artifacts": ["reorganization_plan"],
                    "allowed_tool_groups": ["repo_read"],
                },
            ],
            "artifact_contracts": [
                {"id": "repo_file_inventory", "kind": "input", "required": True},
                {"id": "reorganization_plan", "kind": "contract", "required": True},
            ],
        }
    )

    compiled = compile_artifact_topology(plan)
    discover = compiled.phases[0]
    repo_contract = next(contract for contract in compiled.artifact_contracts if contract.id == "repo_file_inventory")
    executable = compiled.metadata["appv2_artifact_topology"]["executable_phase_contracts"]["discover_01"]

    assert discover.output_artifacts == ["repo_file_inventory"]
    assert repo_contract.kind == "phase_output"
    assert repo_contract.produced_by_phase == "discover_01"
    assert executable["completion_obligations"][0]["artifact_id"] == "repo_file_inventory"
    assert executable["completion_obligations"][0]["synthesis_strategy"] == "repo_inventory_from_discovery_evidence"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_appv2_artifact_topology.py::test_topology_compiler_assigns_downstream_repo_inventory_to_discover -q
```

Expected: fail with `ModuleNotFoundError: No module named 'appV2.artifact_topology'`.

- [ ] **Step 3: Implement topology compiler**

Create `appV2/artifact_topology.py`:

```python
"""Deterministic artifact topology compilation for AppV2.

Planner output is semantic intent. This compiler owns executable artifact
producer/consumer topology before worker execution.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from appV2.executable_contracts import ArtifactPort, CompletionObligation, EvidenceRule, ExecutablePhaseContract
from appV2.schemas import ArtifactContract, PhasePlan, PhaseStep
from appV2.validator import normalize_phase_plan

COMPILER_ID = "appv2_artifact_topology"
COMPILER_VERSION = "phase_01"
KNOWN_RUNTIME_INPUT_IDS = frozenset({"request_envelope"})
DISCOVERY_INVENTORY_IDS = frozenset(
    {
        "repo_file_inventory",
        "repo_inventory",
        "cleanup_inventory",
        "identified_items",
        "workspace_inventory",
        "file_inventory",
    }
)
DISCOVERY_EVIDENCE_RULE = EvidenceRule(
    any_tool=["repo_snapshot", "list_dir", "read_file", "read_many_files", "classify_file_management_candidates"]
)


def compile_artifact_topology(plan: PhasePlan) -> PhasePlan:
    """Compile planner artifact topology into trusted phase output ports."""

    normalized = normalize_phase_plan(plan)
    phases = list(normalized.phases)
    contracts_by_id = {contract.id: contract for contract in normalized.artifact_contracts}
    produced_by = _produced_by(phases)
    consumers_by_id = _consumers_by_id(phases)
    repairs: list[dict[str, Any]] = []

    for artifact_id, consumer_phase_ids in consumers_by_id.items():
        if artifact_id in produced_by or artifact_id in KNOWN_RUNTIME_INPUT_IDS:
            continue
        producer_index = _select_producer_index(artifact_id=artifact_id, consumer_phase_ids=consumer_phase_ids, phases=phases)
        if producer_index is None:
            continue
        producer = phases[producer_index]
        phases[producer_index] = producer.model_copy(update={"output_artifacts": [*producer.output_artifacts, artifact_id]})
        produced_by[artifact_id] = producer.phase_id
        repairs.append(
            {
                "artifact_id": artifact_id,
                "producer_phase_id": producer.phase_id,
                "consumer_phase_ids": consumer_phase_ids,
                "repair": "assigned_missing_producer",
            }
        )

    compiled_contracts = [_compile_contract(contract, produced_by=produced_by) for contract in normalized.artifact_contracts]
    compiled = normalize_phase_plan(normalized.model_copy(update={"phases": phases, "artifact_contracts": compiled_contracts}))
    executable_contracts = _build_executable_contracts(compiled)

    metadata = dict(compiled.metadata)
    metadata[COMPILER_ID] = {
        "version": COMPILER_VERSION,
        "repairs": repairs,
        "known_runtime_input_ids": sorted(KNOWN_RUNTIME_INPUT_IDS),
        "executable_phase_contracts": {
            contract.phase_id: contract.model_dump(mode="json") for contract in executable_contracts
        },
    }
    return compiled.model_copy(update={"metadata": metadata})


def _produced_by(phases: list[PhaseStep]) -> dict[str, str]:
    rows: dict[str, str] = {}
    for phase in phases:
        for artifact_id in phase.output_artifacts:
            rows.setdefault(artifact_id, phase.phase_id)
    return rows


def _consumers_by_id(phases: list[PhaseStep]) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = defaultdict(list)
    for phase in phases:
        for artifact_id in phase.input_artifacts:
            rows[artifact_id].append(phase.phase_id)
    return dict(rows)


def _select_producer_index(*, artifact_id: str, consumer_phase_ids: list[str], phases: list[PhaseStep]) -> int | None:
    first_consumer_index = next((index for index, phase in enumerate(phases) if phase.phase_id in consumer_phase_ids), None)
    if first_consumer_index is None or first_consumer_index == 0:
        return None
    if artifact_id in DISCOVERY_INVENTORY_IDS:
        for index in range(first_consumer_index - 1, -1, -1):
            if phases[index].phase == "DISCOVER":
                return index
    return first_consumer_index - 1


def _compile_contract(contract: ArtifactContract, *, produced_by: dict[str, str]) -> ArtifactContract:
    if contract.id not in produced_by:
        if contract.id in KNOWN_RUNTIME_INPUT_IDS:
            return contract.model_copy(update={"kind": "input", "produced_by_phase": None})
        return contract
    updates: dict[str, Any] = {"produced_by_phase": produced_by[contract.id]}
    if str(contract.kind).strip().lower() == "input":
        updates["kind"] = "phase_output"
    return contract.model_copy(update=updates)


def _build_executable_contracts(plan: PhasePlan) -> list[ExecutablePhaseContract]:
    consumers_by_id = _consumers_by_id(list(plan.phases))
    contracts: list[ExecutablePhaseContract] = []
    for phase in plan.phases:
        input_ports = [
            ArtifactPort(
                id=artifact_id,
                direction="runtime_input" if artifact_id in KNOWN_RUNTIME_INPUT_IDS else "phase_input",
                required=True,
                producer_phase_id=_producer_for(plan, artifact_id),
                consumer_phase_ids=[phase.phase_id],
            )
            for artifact_id in phase.input_artifacts
        ]
        output_ports = [
            ArtifactPort(
                id=artifact_id,
                direction="phase_output",
                required=True,
                producer_phase_id=phase.phase_id,
                consumer_phase_ids=consumers_by_id.get(artifact_id, []),
                evidence_rule=_evidence_rule_for(phase=phase, artifact_id=artifact_id),
                synthesis_strategy=_synthesis_strategy_for(phase=phase, artifact_id=artifact_id),
            )
            for artifact_id in phase.output_artifacts
        ]
        obligations = [
            CompletionObligation(
                artifact_id=port.id,
                required=port.required,
                evidence_rule=port.evidence_rule,
                synthesis_strategy=port.synthesis_strategy,
            )
            for port in output_ports
            if port.required
        ]
        contracts.append(
            ExecutablePhaseContract(
                phase_id=phase.phase_id,
                phase=phase.phase,
                input_ports=input_ports,
                output_ports=output_ports,
                completion_obligations=obligations,
            )
        )
    return contracts


def _producer_for(plan: PhasePlan, artifact_id: str) -> str | None:
    for phase in plan.phases:
        if artifact_id in phase.output_artifacts:
            return phase.phase_id
    return None


def _evidence_rule_for(*, phase: PhaseStep, artifact_id: str) -> EvidenceRule | None:
    if phase.phase == "DISCOVER" and artifact_id in DISCOVERY_INVENTORY_IDS:
        return DISCOVERY_EVIDENCE_RULE
    return None


def _synthesis_strategy_for(*, phase: PhaseStep, artifact_id: str) -> str | None:
    if phase.phase == "DISCOVER" and artifact_id in DISCOVERY_INVENTORY_IDS:
        return "repo_inventory_from_discovery_evidence"
    return None
```

- [ ] **Step 4: Run topology tests**

Run:

```bash
uv run pytest tests/test_appv2_artifact_topology.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add appV2/artifact_topology.py tests/test_appv2_artifact_topology.py
git commit -m "feat(appv2): compile artifact topology into executable contracts"
```

---

### Task 3: Wire topology compiler into policy compiler

**Files:**
- Modify: `appV2/policy_compiler.py`
- Modify: `tests/test_appv2_policy_compiler.py`

- [ ] **Step 1: Add failing compiler integration test**

Append to `tests/test_appv2_policy_compiler.py`:

```python

def test_policy_compiler_runs_artifact_topology_before_validation() -> None:
    envelope = _cleanup_envelope()
    plan = PhasePlan.model_validate(
        {
            "plan_id": "plan_missing_discover_output",
            "request_id": envelope.request_id,
            "objective": "Clean workspace.",
            "strategy": "discover_analyze_mutate_verify_finalize",
            "phases": [
                {
                    "phase_id": "discover_scope",
                    "phase": "DISCOVER",
                    "goal": "Discover cleanup candidates.",
                    "instructions": ["Inspect the workspace scope."],
                    "input_artifacts": ["request_envelope"],
                    "output_artifacts": [],
                    "allowed_tool_groups": [],
                },
                {
                    "phase_id": "analyze_scope",
                    "phase": "ANALYZE",
                    "goal": "Analyze cleanup candidates.",
                    "instructions": ["Build reorganization plan."],
                    "input_artifacts": ["cleanup_inventory"],
                    "output_artifacts": ["reorganization_plan"],
                    "allowed_tool_groups": [],
                },
                {
                    "phase_id": "mutate_workspace",
                    "phase": "MUTATE",
                    "goal": "Move files and write manifest.",
                    "instructions": ["Apply scoped file operations."],
                    "input_artifacts": ["reorganization_plan"],
                    "output_artifacts": ["mutation_record"],
                    "allowed_tool_groups": [],
                },
                {
                    "phase_id": "verify_workspace",
                    "phase": "VERIFY",
                    "goal": "Verify cleanup.",
                    "instructions": ["Verify final file state."],
                    "input_artifacts": ["mutation_record"],
                    "output_artifacts": ["verification_evidence"],
                    "allowed_tool_groups": [],
                },
            ],
            "artifact_contracts": [
                {"id": "request_envelope", "kind": "input"},
                {"id": "cleanup_inventory", "kind": "input"},
                {"id": "reorganization_plan"},
                {"id": "mutation_record"},
                {"id": "verification_evidence"},
            ],
        }
    )

    compiled = compile_phase_plan_policy(plan, envelope=envelope)
    discover = next(phase for phase in compiled.phases if phase.phase_id == "discover_scope")
    cleanup_contract = next(contract for contract in compiled.artifact_contracts if contract.id == "cleanup_inventory")

    assert discover.output_artifacts == ["cleanup_inventory"]
    assert cleanup_contract.kind == "phase_output"
    assert cleanup_contract.produced_by_phase == "discover_scope"
    assert "appv2_artifact_topology" in compiled.metadata
    assert AppV2Validator().validate_phase_plan(compiled, envelope=envelope) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_appv2_policy_compiler.py::test_policy_compiler_runs_artifact_topology_before_validation -q
```

Expected: fail because `compile_phase_plan_policy` does not call topology compiler.

- [ ] **Step 3: Call topology compiler before existing policy compilation**

Modify `appV2/policy_compiler.py`:

```python
from appV2.artifact_topology import compile_artifact_topology
```

Change the start of `compile_phase_plan_policy` to:

```python
def compile_phase_plan_policy(
    plan: PhasePlan,
    *,
    envelope: Envelope | None = None,
    root_path: str | Path | None = None,
) -> PhasePlan:
    """Compile runtime-owned artifact topology, tool groups, and policies."""

    normalized = compile_artifact_topology(normalize_phase_plan(plan))
    compiled_phases: list[PhaseStep] = []
    mutation_phase_count = 0
    verification_phase_count = 0
```

Keep the rest of the function unchanged except ensure metadata is copied from `normalized.metadata`, preserving `appv2_artifact_topology`.

- [ ] **Step 4: Run compiler tests**

Run:

```bash
uv run pytest tests/test_appv2_policy_compiler.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add appV2/policy_compiler.py tests/test_appv2_policy_compiler.py
git commit -m "feat(appv2): run artifact topology compiler before policy compilation"
```

---

### Task 4: Harden validator runtime-input semantics

**Files:**
- Modify: `appV2/validator.py`
- Modify: `tests/test_appv2_validator.py`

- [ ] **Step 1: Add failing validator test for uncompiled runtime input misuse**

Append to `tests/test_appv2_validator.py`:

```python

def test_validator_blocks_non_runtime_input_consumed_without_producer() -> None:
    plan = PhasePlan.model_validate(
        {
            "plan_id": "plan_bad_input",
            "request_id": "req_1",
            "objective": "Bad artifact topology.",
            "strategy": "analyze_without_discover_output",
            "phases": [
                {
                    "phase_id": "analyze",
                    "phase": "ANALYZE",
                    "goal": "Analyze inventory.",
                    "instructions": ["Use inventory."],
                    "input_artifacts": ["repo_file_inventory"],
                    "output_artifacts": ["reorganization_plan"],
                    "allowed_tool_groups": ["repo_read"],
                    "max_model_calls": 2,
                }
            ],
            "artifact_contracts": [
                {"id": "repo_file_inventory", "kind": "input"},
                {"id": "reorganization_plan"},
            ],
        }
    )

    issues = AppV2Validator().validate_phase_plan(plan, envelope=_envelope())

    assert any(issue.code == "missing_artifact_producer" for issue in issues)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_appv2_validator.py::test_validator_blocks_non_runtime_input_consumed_without_producer -q
```

Expected: fail if `repo_file_inventory` is treated as runtime scope.

- [ ] **Step 3: Narrow `runtime_scope_input_ids`**

Modify `appV2/validator.py` so `runtime_scope_input_ids` only includes explicit runtime scope IDs and contracts marked by metadata:

```python
def runtime_scope_input_ids(envelope: Envelope | None, *, plan: PhasePlan | None = None) -> set[str]:
    """Return runtime-supplied scope inputs that are available without phase production."""

    if envelope is None:
        return set()
    runtime_ids = set(RUNTIME_SCOPE_INPUT_IDS)
    if plan is not None:
        runtime_ids.update(
            contract.id
            for contract in plan.artifact_contracts
            if contract.produced_by_phase is None
            and _is_runtime_scope_contract(contract)
            and contract.metadata.get("runtime_scope") is True
        )
    return runtime_ids
```

Keep `request_envelope` available through `RUNTIME_SCOPE_INPUT_IDS`.

- [ ] **Step 4: Run validator and compiler tests**

Run:

```bash
uv run pytest tests/test_appv2_validator.py tests/test_appv2_policy_compiler.py tests/test_appv2_artifact_topology.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add appV2/validator.py tests/test_appv2_validator.py
git commit -m "fix(appv2): restrict runtime-scope artifact inputs"
```

---

### Task 5: Make worker context consume executable contract obligations

**Files:**
- Modify: `appV2/worker/context.py`
- Modify: `tests/test_appv2_worker_loop.py`

- [ ] **Step 1: Add failing regression test for live-probe missing DISCOVER output**

Append to `tests/test_appv2_worker_loop.py`:

```python

def test_worker_synthesizes_discover_output_from_compiled_topology_when_planner_omits_output(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "discover_01",
                    "phase": "DISCOVER",
                    "goal": "Inspect repository.",
                    "instructions": ["Gather repo inventory."],
                    "input_artifacts": [],
                    "output_artifacts": [],
                    "allowed_tool_groups": ["repo_read"],
                    "max_tool_calls": 5,
                    "max_model_calls": 3,
                },
                {
                    "phase_id": "analyze_01",
                    "phase": "ANALYZE",
                    "goal": "Analyze inventory.",
                    "instructions": ["Use repo_file_inventory."],
                    "input_artifacts": ["repo_file_inventory"],
                    "output_artifacts": ["reorganization_plan"],
                    "allowed_tool_groups": ["repo_read"],
                    "max_tool_calls": 1,
                    "max_model_calls": 1,
                },
            ],
            "artifact_contracts": [
                {"id": "repo_file_inventory", "kind": "input"},
                {"id": "reorganization_plan"},
            ],
        }
    )
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "call_id": "scan",
                        "tool_name": "repo_snapshot",
                        "arguments": {},
                        "purpose": "capture repository state",
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "call_id": "scan_again",
                        "tool_name": "repo_snapshot",
                        "arguments": {},
                        "purpose": "capture repository state",
                    }
                ]
            },
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "analysis complete",
                    "artifacts": [
                        {
                            "id": "reorganization_plan",
                            "kind": "phase_output",
                            "content": {"operations": []},
                            "producer": "worker",
                            "lifecycle": "completed",
                        }
                    ],
                }
            },
        ]
    )
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")

    result = WorkerRuntime(model_client=client, root_path=tmp_path).run(plan, envelope=_envelope())

    assert result.status == "completed"
    assert any(artifact.id == "repo_file_inventory" for artifact in result.artifacts)
    assert any(row["event"] == "discover_output_synthesized" for row in result.metadata["runtime_matrix"]["rows"])
```

- [ ] **Step 2: Run test to verify current failure**

Run:

```bash
uv run pytest tests/test_appv2_worker_loop.py::test_worker_synthesizes_discover_output_from_compiled_topology_when_planner_omits_output -q
```

Expected: fail before Task 3 is wired into `WorkerRuntime.run` or if context ignores compiled outputs.

- [ ] **Step 3: Ensure `WorkerRuntime.run` compiles topology through existing policy compiler**

No new code is needed if Task 3 changed `compile_phase_plan_policy`; `WorkerRuntime.run` already starts with:

```python
phase_plan = compile_phase_plan_policy(normalize_phase_plan(phase_plan), envelope=envelope, root_path=self._root_path)
```

If this line was changed by other work, restore the call to `compile_phase_plan_policy` as the first executable transformation in `WorkerRuntime.run`.

- [ ] **Step 4: Run the regression test**

Run:

```bash
uv run pytest tests/test_appv2_worker_loop.py::test_worker_synthesizes_discover_output_from_compiled_topology_when_planner_omits_output -q
```

Expected: pass after topology compiler wiring.

- [ ] **Step 5: Commit**

```bash
git add tests/test_appv2_worker_loop.py appV2/worker/runtime.py
git commit -m "test(appv2): cover discover synthesis from compiled topology"
```

---

### Task 6: Make DISCOVER synthesis use compiled executable metadata explicitly

**Files:**
- Modify: `appV2/worker/context.py`
- Modify: `appV2/worker/agent_loop.py`
- Modify: `tests/test_appv2_worker_loop.py`

- [ ] **Step 1: Add failing test for metadata-backed completion obligations**

Append to `tests/test_appv2_worker_loop.py`:

```python

def test_discover_synthesis_uses_compiled_completion_obligation_metadata(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "discover_01",
                    "phase": "DISCOVER",
                    "goal": "Inspect repository.",
                    "instructions": ["Gather repo inventory."],
                    "input_artifacts": [],
                    "output_artifacts": ["custom_inventory"],
                    "allowed_tool_groups": ["repo_read"],
                    "max_tool_calls": 2,
                    "max_model_calls": 2,
                }
            ],
            "artifact_contracts": [{"id": "custom_inventory"}],
            "metadata": {
                "appv2_artifact_topology": {
                    "version": "phase_01",
                    "repairs": [],
                    "known_runtime_input_ids": ["request_envelope"],
                    "executable_phase_contracts": {
                        "discover_01": {
                            "phase_id": "discover_01",
                            "phase": "DISCOVER",
                            "input_ports": [],
                            "output_ports": [
                                {
                                    "id": "custom_inventory",
                                    "direction": "phase_output",
                                    "required": True,
                                    "producer_phase_id": "discover_01",
                                    "consumer_phase_ids": [],
                                    "evidence_rule": {"any_tool": ["repo_snapshot"], "all_tools": [], "trust_levels": ["tool_observed"]},
                                    "synthesis_strategy": "repo_inventory_from_discovery_evidence",
                                }
                            ],
                            "completion_obligations": [
                                {
                                    "artifact_id": "custom_inventory",
                                    "required": True,
                                    "evidence_rule": {"any_tool": ["repo_snapshot"], "all_tools": [], "trust_levels": ["tool_observed"]},
                                    "synthesis_strategy": "repo_inventory_from_discovery_evidence",
                                }
                            ],
                        }
                    },
                }
            },
        }
    )
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {"call_id": "scan", "tool_name": "repo_snapshot", "arguments": {}, "purpose": "scan repository"}
                ]
            },
            {
                "tool_calls": [
                    {"call_id": "scan_again", "tool_name": "repo_snapshot", "arguments": {}, "purpose": "scan repository"}
                ]
            },
        ]
    )
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")

    result = WorkerRuntime(model_client=client, root_path=tmp_path).run(plan, envelope=_envelope())

    assert result.status == "completed"
    assert any(artifact.id == "custom_inventory" for artifact in result.artifacts)
```

- [ ] **Step 2: Run test to verify it fails if synthesis only uses hard-coded artifact names**

Run:

```bash
uv run pytest tests/test_appv2_worker_loop.py::test_discover_synthesis_uses_compiled_completion_obligation_metadata -q
```

Expected: fail if `custom_inventory` has no hard-coded discovery strategy.

- [ ] **Step 3: Add executable contract lookup to context frame**

Modify `appV2/worker/context.py`:

1. Add field to `PhaseFrame`:

```python
    executable_contract: dict[str, Any]
```

2. In `build_phase_frame`, add:

```python
            executable_contract=_executable_contract_for(plan=plan, phase=phase),
```

3. Add helper:

```python
def _executable_contract_for(*, plan: PhasePlan, phase: PhaseStep) -> dict[str, Any]:
    topology = plan.metadata.get("appv2_artifact_topology") if isinstance(plan.metadata, dict) else None
    if not isinstance(topology, dict):
        return {}
    contracts = topology.get("executable_phase_contracts")
    if not isinstance(contracts, dict):
        return {}
    contract = contracts.get(phase.phase_id)
    return contract if isinstance(contract, dict) else {}
```

- [ ] **Step 4: Use executable obligations in `_maybe_complete_discover_phase`**

Modify `appV2/worker/agent_loop.py` inside `_maybe_complete_discover_phase`:

Replace:

```python
        if phase.phase != "DISCOVER" or not frame.pending_outputs:
            return None
```

with:

```python
        if phase.phase != "DISCOVER":
            return None
        pending_outputs = _pending_outputs_from_frame(frame)
        if not pending_outputs:
            return None
```

Replace:

```python
        for artifact_id in frame.pending_outputs:
```

with:

```python
        for artifact_id in pending_outputs:
```

Add helper near other helper functions:

```python
def _pending_outputs_from_frame(frame: Any) -> list[str]:
    pending = [artifact_id for artifact_id in getattr(frame, "pending_outputs", []) if isinstance(artifact_id, str)]
    if pending:
        return pending
    executable = getattr(frame, "executable_contract", {})
    if not isinstance(executable, dict):
        return []
    obligations = executable.get("completion_obligations")
    if not isinstance(obligations, list):
        return []
    rows: list[str] = []
    for obligation in obligations:
        if not isinstance(obligation, dict) or obligation.get("required") is False:
            continue
        artifact_id = obligation.get("artifact_id")
        if isinstance(artifact_id, str) and artifact_id:
            rows.append(artifact_id)
    return rows
```

- [ ] **Step 5: Run worker loop tests**

Run:

```bash
uv run pytest tests/test_appv2_worker_loop.py::test_discover_synthesis_uses_compiled_completion_obligation_metadata tests/test_appv2_worker_loop.py::test_worker_denies_duplicate_discover_read_and_completes_from_existing_evidence -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add appV2/worker/context.py appV2/worker/agent_loop.py tests/test_appv2_worker_loop.py
git commit -m "feat(appv2): drive discover completion from executable contracts"
```

---

### Task 7: Fix probe runner model override behavior

**Files:**
- Modify: `scripts/live_appv2_runtime_probe.py`
- Test: `tests/test_appv2_runtime_services.py` or new `tests/test_live_appv2_runtime_probe.py`

- [ ] **Step 1: Add failing unit test for `.env` model precedence**

Create `tests/test_live_appv2_runtime_probe.py`:

```python
import argparse
import os
from pathlib import Path

from scripts.live_appv2_runtime_probe import _configure_worker_env


def test_configure_worker_env_does_not_override_env_model_when_arg_is_none(monkeypatch) -> None:
    monkeypatch.setenv("APPV2_WORKER_LLM_MODEL", "qwen/qwen3-coder-next")
    args = argparse.Namespace(
        worker_model=None,
        worker_timeout="90",
        max_tokens="2400",
    )

    _configure_worker_env(args)

    assert os.environ["APPV2_WORKER_LLM_MODEL"] == "qwen/qwen3-coder-next"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_live_appv2_runtime_probe.py::test_configure_worker_env_does_not_override_env_model_when_arg_is_none -q
```

Expected: fail because parser default currently supplies a model and `_configure_worker_env` always sets it.

- [ ] **Step 3: Make `--worker-model` optional**

Modify `scripts/live_appv2_runtime_probe.py` parser:

```python
parser.add_argument("--worker-model", default=None)
```

Modify output filename model slug fallback:

```python
worker_model_for_path = args.worker_model or os.environ.get("APPV2_WORKER_LLM_MODEL") or "env-worker-model"
out_path = out_dir / f"live-appv2-{args.scenario}-{_slug(worker_model_for_path)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
```

Modify `_configure_worker_env`:

```python
def _configure_worker_env(args: argparse.Namespace) -> None:
    os.environ["APPV2_WORKER_LLM_ENABLED"] = "true"
    if args.worker_model:
        os.environ["APPV2_WORKER_LLM_MODEL"] = args.worker_model
    os.environ["APPV2_WORKER_LLM_PROVIDER_SORT"] = "latency"
    os.environ["APPV2_WORKER_LLM_TIMEOUT_SECONDS"] = str(args.worker_timeout)
    os.environ["APPV2_WORKER_LLM_TEMPERATURE"] = "0"
    os.environ["APPV2_WORKER_LLM_RESPONSE_FORMAT"] = "json_schema"
    os.environ["APPV2_WORKER_LLM_MAX_TOKENS"] = str(args.max_tokens)
```

- [ ] **Step 4: Run test**

Run:

```bash
uv run pytest tests/test_live_appv2_runtime_probe.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/live_appv2_runtime_probe.py tests/test_live_appv2_runtime_probe.py
git commit -m "fix(appv2): preserve env worker model in live probe"
```

---

### Task 8: Improve provider error observability without leaking secrets

**Files:**
- Modify: `appV2/model_client.py`
- Modify: `tests/test_appv2_runtime_services.py` or create `tests/test_appv2_model_client.py`

- [ ] **Step 1: Add failing test for preserved provider error class/message**

Create `tests/test_appv2_model_client.py`:

```python
import pytest

from appV2.model_client import AppV2JSONClient


class BrokenChat:
    def send(self, **kwargs):
        raise ValueError("No endpoints found for model google/bad-model with key sk-secret")


class BrokenOpenRouter:
    def __init__(self, *args, **kwargs) -> None:
        self.chat = BrokenChat()


def test_model_client_preserves_redacted_provider_error(monkeypatch) -> None:
    monkeypatch.setattr("appV2.model_client.OpenRouter", BrokenOpenRouter)
    client = AppV2JSONClient(api_key="sk-secret", model="google/bad-model")

    with pytest.raises(RuntimeError) as exc_info:
        client.complete_json(stage="appv2_worker_discover", prompt="{}", schema={"type": "object"})

    message = str(exc_info.value)
    assert "provider_error_type=ValueError" in message
    assert "No endpoints found" in message
    assert "sk-secret" not in message
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_appv2_model_client.py::test_model_client_preserves_redacted_provider_error -q
```

Expected: fail because current message is generic.

- [ ] **Step 3: Implement redacted provider error detail**

Modify `appV2/model_client.py`:

```python
        try:
            response = self._client.chat.send(**kwargs)
        except Exception as exc:  # pragma: no cover - network/provider variability
            error_message = _redact_secret(str(exc), self._client_secret_candidates())
            raise RuntimeError(
                f"Model request for stage {stage} failed before receiving a response "
                f"provider_error_type={type(exc).__name__} provider_error={error_message}"
            ) from exc
```

Add methods/helpers:

```python
    def _client_secret_candidates(self) -> list[str]:
        return []


def _redact_secret(message: str, secrets: list[str]) -> str:
    redacted = message
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted
```

Update `__init__` to store the API key only for redaction candidates:

```python
        self._api_key_for_redaction = api_key
```

Update `_client_secret_candidates`:

```python
    def _client_secret_candidates(self) -> list[str]:
        return [self._api_key_for_redaction]
```

- [ ] **Step 4: Run model-client test**

Run:

```bash
uv run pytest tests/test_appv2_model_client.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add appV2/model_client.py tests/test_appv2_model_client.py
git commit -m "fix(appv2): preserve redacted provider error details"
```

---

### Task 9: Update docs for the trusted interface

**Files:**
- Modify: `docs/appv2-runtime.md`
- Modify: `docs/appv2-policy-compiler.md`
- Modify: `docs/pi-appv2-agentic-architecture.md`

- [ ] **Step 1: Update `docs/appv2-runtime.md` architecture section**

Add this section after `Runtime-Compiled Policy`:

```markdown
## Runtime-Compiled Artifact Topology

Planner phase plans are semantic intent, not executable authority. Before worker execution, AppV2 compiles artifact topology into executable phase contracts.

The trusted worker-facing contract contains:

- runtime inputs such as `request_envelope`
- phase inputs produced by earlier phases
- phase outputs required at phase boundaries
- evidence rules that state which tool observations can satisfy an output
- synthesis strategies for deterministic derived artifacts

This prevents a planner from accidentally treating `repo_file_inventory` or similar phase outputs as runtime inputs. Tool observations remain evidence records; phase outputs are explicit derived artifacts consumed by downstream phases.
```

- [ ] **Step 2: Update `docs/appv2-policy-compiler.md`**

Add this section after the mutation compiler description:

```markdown
## Artifact Topology Compiler

`appV2.artifact_topology.compile_artifact_topology` runs before mutation and verification policy compilation. It repairs deterministic artifact omissions, including DISCOVER inventory outputs consumed by later ANALYZE or MUTATE phases.

Known runtime inputs are intentionally narrow. `request_envelope` is runtime-supplied. Other artifacts must be produced by a prior phase unless explicitly marked as runtime scope metadata.

The compiler writes `metadata.appv2_artifact_topology` with executable phase contracts for audit and worker consumption.
```

- [ ] **Step 3: Update PI comparison document**

Add this note to `docs/pi-appv2-agentic-architecture.md` in the AppV2 section:

```markdown
The 2026-06-15 artifact-topology refactor narrows the gap with PI's message/tool-result loop. PI naturally carries tool results forward as conversation state. AppV2 now keeps that benefit as explicit evidence records while compiling phase-boundary artifacts for deterministic downstream consumption.
```

- [ ] **Step 4: Commit docs**

```bash
git add docs/appv2-runtime.md docs/appv2-policy-compiler.md docs/pi-appv2-agentic-architecture.md
git commit -m "docs(appv2): document executable artifact topology"
```

---

### Task 10: Run focused regression suite and live probe

**Files:**
- No source edits expected.

- [ ] **Step 1: Run focused unit tests**

Run:

```bash
uv run pytest tests/test_appv2_artifact_topology.py tests/test_appv2_policy_compiler.py tests/test_appv2_validator.py tests/test_appv2_worker_loop.py tests/test_live_appv2_runtime_probe.py tests/test_appv2_model_client.py -q
```

Expected: all pass.

- [ ] **Step 2: Run existing AppV2 focused suite**

Run:

```bash
uv run pytest tests/test_appv2_usage_matrix.py tests/test_appv2_events_sessions.py tests/test_appv2_policy_compiler.py tests/test_appv2_runtime_services.py tests/test_appv2_extensions.py tests/test_appv2_worker_tools.py -q
```

Expected: all pass.

- [ ] **Step 3: Run live probe using `.env` worker model**

Run:

```bash
uv run python scripts/live_appv2_runtime_probe.py --scenario file_workspace_cleanup --matrix-poll-interval 5 --out-dir plan
```

Expected:

- worker model in output `worker_env.APPV2_WORKER_LLM_MODEL` matches `.env` or process env
- planner policy compile succeeds
- `DISCOVER` emits `discover_output_synthesized` or equivalent completed phase output
- worker reaches `MUTATE`
- result status is `completed` or a later non-DISCOVER failure with concrete mutation/verification evidence

- [ ] **Step 4: If live probe still fails after DISCOVER, classify the new failure**

Use the saved JSON output and classify the failure as one of:

```text
planner_semantic_failure
mutation_policy_denial
worker_operation_generation_failure
verification_evidence_failure
provider_runtime_failure
```

Record the classification in the final implementation report with the output path.

- [ ] **Step 5: Commit final verification note if docs changed**

If the implementation report is saved under `plan/` or `docs/`, commit it:

```bash
git add plan docs
git commit -m "test(appv2): record executable topology probe results"
```

---

## Self-Review

- Spec coverage: The plan covers the trusted interface, artifact topology compiler, runtime input narrowing, evidence-backed DISCOVER completion, probe model override, provider observability, docs, and live probe verification.
- Placeholder scan: No task contains `TBD`, `TODO`, or an unspecified implementation step. Each code task includes exact test and implementation snippets.
- Type consistency: `ArtifactPort`, `EvidenceRule`, `CompletionObligation`, and `ExecutablePhaseContract` are introduced before use. Metadata key `appv2_artifact_topology` is consistent across compiler, context, worker, and docs.
- Scope check: This is one cohesive refactor. It does not mix unrelated UI, provider registry, or extension platform changes.
