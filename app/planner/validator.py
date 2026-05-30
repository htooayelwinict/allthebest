"""Deterministic planner output validation."""

from __future__ import annotations

from app.planner.contracts import ALLOWED_WORKER_TYPES, PlannerValidationError
from app.schemas import Envelope, Plan


DISCOVERY_CONTEXT_SIGNALS = {
    "target_file",
    "repo_tree",
    "scope_clarification",
}
DISCOVERY_CONSTRAINT_SIGNALS = {
    "target_locations_must_be_identified_before_mutation",
    "target_scope_must_be_identified_before_mutation",
}
DEPENDENCY_SIGNALS = {
    "dependency",
    "manifest",
    "package",
}
WRITE_SCOPE_ARTIFACT_SIGNALS = {
    "allowed_write_paths",
    "mutation_scope",
    "patch_scope",
    "writable_targets",
}
PHASE_ORDER = (
    "DISCOVER",
    "ANALYZE",
    "RESEARCH",
    "DESIGN",
    "MUTATE",
    "VERIFY",
    "FINALIZE",
)
PHASE_INDEX = {phase: index for index, phase in enumerate(PHASE_ORDER)}
PHASE_MODES: dict[str, set[str]] = {
    "DISCOVER": {"observe_only"},
    "ANALYZE": {"observe_only"},
    "RESEARCH": {"observe_only"},
    "DESIGN": {"plan_only"},
    "MUTATE": {"bounded_mutation"},
    "VERIFY": {"verify_only"},
    "FINALIZE": {"summarize_only"},
}


class PlannerPlanValidator:
    """Validates generated plans before worker-kernel execution."""

    _WRITE_CAPABLE_WORKERS = {"code_worker"}

    def validate(self, envelope: Envelope, plan: Plan) -> Plan:
        errors: list[str] = []

        if plan.request_id != envelope.request_id:
            errors.append("plan.request_id must match envelope.request_id")

        if not (plan.plan_id or "").strip():
            errors.append("plan.plan_id must be non-empty")

        if not plan.steps:
            errors.append("plan.steps must contain at least one step")

        step_ids = [step.step_id for step in plan.steps]
        if len(set(step_ids)) != len(step_ids):
            errors.append("plan.steps step_id values must be unique")

        phase_present = any(step.phase is not None for step in plan.steps)
        mode_present = any(step.mode is not None for step in plan.steps)
        task_id_present = any(step.task_id is not None for step in plan.steps)
        phase_contract_required = phase_present or bool((plan.execution_pattern or "").strip()) or bool(
            plan.global_invariants
        )
        mode_contract_required = phase_contract_required or mode_present
        task_contract_required = phase_contract_required or task_id_present

        if phase_contract_required and any(step.phase is None for step in plan.steps):
            errors.append("phase-aware plans must populate step.phase for every step")
        if mode_contract_required and any(step.mode is None for step in plan.steps):
            errors.append("phase-aware plans must populate step.mode for every step")
        if task_contract_required and any(step.task_id is None or not step.task_id.strip() for step in plan.steps):
            errors.append("phase-aware plans must populate non-empty step.task_id for every step")

        if phase_contract_required:
            if not (plan.execution_pattern or "").strip():
                errors.append("phase-aware plans must populate plan.execution_pattern")
            if not plan.global_invariants:
                errors.append("phase-aware plans must populate plan.global_invariants")

        for step in plan.steps:
            if step.worker_type not in ALLOWED_WORKER_TYPES:
                errors.append(f"unknown worker_type: {step.worker_type}")
            if step.max_tool_calls < 0:
                errors.append(f"step {step.step_id} max_tool_calls must be non-negative")
            if step.max_model_calls < 0:
                errors.append(f"step {step.step_id} max_model_calls must be non-negative")
            if step.phase is not None and step.phase not in PHASE_INDEX:
                errors.append(f"step {step.step_id} has invalid phase: {step.phase}")
            if step.mode is not None:
                if step.phase is None:
                    errors.append(f"step {step.step_id} has mode but no phase")
                elif not step.mode.strip():
                    errors.append(f"step {step.step_id} mode must be a non-empty string")
            missing_permission_keys = [
                key for key in ("read_files", "write_files", "run_commands") if key not in step.permissions
            ]
            if missing_permission_keys:
                errors.append(
                    f"step {step.step_id} permissions must explicitly include read_files/write_files/run_commands"
                )
            else:
                for key in ("read_files", "write_files", "run_commands"):
                    if not isinstance(step.permissions.get(key), bool):
                        errors.append(f"step {step.step_id} permission {key} must be a boolean")

        required_tools = sum(step.max_tool_calls for step in plan.steps)
        required_models = sum(step.max_model_calls for step in plan.steps)
        max_tool_calls = int(plan.budget.get("max_tool_calls", 0) or 0)
        max_model_calls = int(plan.budget.get("max_model_calls", 0) or 0)
        max_workers = int(plan.budget.get("max_workers", 0) or 0)

        if max_tool_calls < required_tools:
            errors.append("plan budget max_tool_calls must cover sum of step max_tool_calls")
        if max_model_calls < required_models:
            errors.append("plan budget max_model_calls must cover sum of step max_model_calls")
        if max_workers < len(plan.steps):
            errors.append("plan budget max_workers must cover step count")

        produced: set[str] = set()
        produced_by: dict[str, int] = {}
        write_step_indexes: list[int] = []
        for index, step in enumerate(plan.steps):
            for artifact_id in step.input_artifacts:
                if artifact_id not in produced:
                    errors.append(
                        f"step {step.step_id} input_artifact '{artifact_id}' is not produced by an earlier step"
                    )

            write_files = bool(step.permissions.get("write_files", False))
            if write_files:
                write_step_indexes.append(index)
                if step.worker_type not in self._WRITE_CAPABLE_WORKERS:
                    errors.append(
                        f"step {step.step_id} requests write_files but worker_type {step.worker_type} is not write-capable"
                    )
                errors.extend(
                    self._write_scope_errors(
                        step_id=step.step_id,
                        permissions=step.permissions,
                        produced=produced,
                        produced_by=produced_by,
                        steps=plan.steps,
                    )
                )
                if step.phase is not None and step.phase != "MUTATE":
                    errors.append(f"step {step.step_id} writes files but phase is not MUTATE")
            elif step.phase == "MUTATE":
                errors.append(f"step {step.step_id} phase MUTATE must set permissions.write_files=true")

            for artifact_id in step.output_artifacts:
                produced.add(artifact_id)
                produced_by.setdefault(artifact_id, index)

        if write_step_indexes:
            first_write = min(write_step_indexes)
            if self._requires_discovery_before_mutation(envelope):
                if not any(self._is_discovery_step(plan.steps[i].worker_type, plan.steps[i].permissions) for i in range(first_write)):
                    errors.append("mutation requires a prior read-only discovery step")

            last_write = max(write_step_indexes)
            if not any(step.worker_type == "verify_worker" for step in plan.steps[last_write + 1 :]):
                errors.append("mutation requires a later verify_worker step")

            evidence_groups = self._required_evidence_groups(envelope)
            evidence_steps_by_group: dict[str, set[int]] = {}
            for group in evidence_groups:
                group_needles = ("evidence",) if group == "general" else (group, "evidence")
                evidence_steps = self._producer_steps_before(
                    produced_by=produced_by,
                    before_index=first_write,
                    needles=group_needles,
                )
                if not evidence_steps:
                    label = "evidence" if group == "general" else f"{group} evidence"
                    errors.append(f"{label} must be produced before mutation")
                if not self._write_steps_consume_artifact_group(
                    plan=plan,
                    write_step_indexes=write_step_indexes,
                    needles=group_needles,
                ):
                    label = "evidence" if group == "general" else f"{group} evidence"
                    errors.append(f"mutation must consume {label}")
                evidence_steps_by_group[group] = evidence_steps

            dependency_required = self._requires_dependency_evidence(envelope)
            dependency_needles = ("dependency", "manifest", "package")
            if dependency_required:
                dependency_steps = self._producer_steps_before(
                    produced_by=produced_by,
                    before_index=first_write,
                    needles=dependency_needles,
                )
                if not dependency_steps:
                    errors.append("dependency evidence must be produced before mutation")
                if not self._write_steps_consume_artifact_group(
                    plan=plan,
                    write_step_indexes=write_step_indexes,
                    needles=dependency_needles,
                ):
                    errors.append("mutation must consume dependency evidence")
            else:
                dependency_steps = set()

            if envelope.complexity_hint == "high" and dependency_steps and evidence_steps_by_group:
                if not self._has_separate_dependency_and_evidence_sources(
                    plan=plan,
                    write_step_indexes=write_step_indexes,
                    produced_by=produced_by,
                    evidence_groups=evidence_groups,
                    dependency_needles=dependency_needles,
                ):
                    errors.append("dependency discovery and evidence collection require separate steps")

            if not any(
                self._artifact_matches(artifact_id, ("rollback", "revert"))
                for index in write_step_indexes
                for artifact_id in plan.steps[index].output_artifacts
            ):
                errors.append("mutation steps must output a rollback/revert artifact")

            if not any(
                step.worker_type == "verify_worker"
                and any(self._artifact_matches(artifact_id, ("verification", "test")) for artifact_id in step.output_artifacts)
                for step in plan.steps[last_write + 1 :]
            ):
                errors.append("verification after mutation must output verification/test artifacts")

            if not self._metadata_list(plan, "stop_conditions"):
                errors.append("mutating plans must include metadata.stop_conditions")
            if not self._metadata_list(plan, "replan_triggers"):
                errors.append("mutating plans must include metadata.replan_triggers")

            if envelope.confidence < 0.7 and not any(
                self._is_discovery_step(plan.steps[i].worker_type, plan.steps[i].permissions)
                for i in range(min(write_step_indexes))
            ):
                errors.append("low-confidence envelopes cannot mutate before discovery")

        if phase_contract_required:
            self._validate_phase_progression(envelope=envelope, plan=plan, errors=errors)

        if errors:
            raise PlannerValidationError(errors)
        return plan

    def _requires_discovery_before_mutation(self, envelope: Envelope) -> bool:
        context = {self._normalize(value) for value in envelope.context_needed}
        constraints = {self._normalize(value) for value in envelope.constraints}
        return bool(context & DISCOVERY_CONTEXT_SIGNALS) or bool(constraints & DISCOVERY_CONSTRAINT_SIGNALS)

    def _required_evidence_groups(self, envelope: Envelope) -> set[str]:
        groups: set[str] = set()
        for value in envelope.context_needed:
            normalized = self._normalize(value)
            if normalized.endswith("_evidence"):
                group = normalized[: -len("_evidence")]
                groups.add(group or "general")
            elif "evidence" in normalized:
                groups.add("general")

        for value in envelope.constraints:
            normalized = self._normalize(value)
            if "require_evidence" in normalized or "requires_evidence" in normalized:
                prefix = normalized.split("_require", 1)[0]
                prefix = prefix.removesuffix("_claims")
                groups.add(prefix or "general")

        return groups

    def _requires_dependency_evidence(self, envelope: Envelope) -> bool:
        values = [*envelope.intents, *envelope.domains, *envelope.risks, *envelope.context_needed, *envelope.constraints]
        values.extend(str(artifact.get("name", "")) for artifact in envelope.artifacts)
        values.extend(str(artifact.get("type", "")) for artifact in envelope.artifacts)
        return self._has_any_signal(values, DEPENDENCY_SIGNALS)

    def _is_discovery_step(self, worker_type: str, permissions: dict) -> bool:
        return worker_type in {"repo_worker", "research_worker", "infra_worker"} and not bool(
            permissions.get("write_files", False)
        )

    def _write_scope_errors(
        self,
        *,
        step_id: str,
        permissions: dict,
        produced: set[str],
        produced_by: dict[str, int],
        steps: list,
    ) -> list[str]:
        write_paths = permissions.get("write_paths")
        write_path_artifacts = permissions.get("write_paths_from_artifacts")

        if isinstance(write_paths, list) and any(self._is_specific_path(value) for value in write_paths):
            return []
        if isinstance(write_path_artifacts, list) and write_path_artifacts:
            missing = [artifact_id for artifact_id in write_path_artifacts if artifact_id not in produced]
            if not missing:
                invalid_scope_artifacts = []
                for artifact_id in write_path_artifacts:
                    producer_index = produced_by.get(artifact_id)
                    producer_phase = steps[producer_index].phase if producer_index is not None else None
                    if producer_phase != "DESIGN" or not self._artifact_matches(
                        artifact_id,
                        tuple(WRITE_SCOPE_ARTIFACT_SIGNALS),
                    ):
                        invalid_scope_artifacts.append(artifact_id)
                if invalid_scope_artifacts:
                    return [
                        f"step {step_id} write_paths_from_artifacts must reference DESIGN-produced write-scope artifacts: {', '.join(invalid_scope_artifacts)}"
                    ]
                return []
            return [
                f"step {step_id} write_paths_from_artifacts must reference earlier path artifacts: {', '.join(missing)}"
            ]
        return [f"step {step_id} with write_files must restrict writes by write_paths or write_paths_from_artifacts"]

    def _is_specific_path(self, value: object) -> bool:
        if not isinstance(value, str):
            return False
        stripped = value.strip()
        if not stripped or stripped in {"*", ".", "./", "/"}:
            return False
        return "*" not in stripped

    def _has_any_signal(self, values: list[str], needles: set[str]) -> bool:
        normalized_values = [self._normalize(value) for value in values]
        return any(needle in value for value in normalized_values for needle in needles)

    def _producer_steps_before(
        self,
        *,
        produced_by: dict[str, int],
        before_index: int,
        needles: tuple[str, ...],
    ) -> set[int]:
        return {
            step_index
            for artifact_id, step_index in produced_by.items()
            if step_index < before_index and self._artifact_matches(artifact_id, needles)
        }

    def _write_steps_consume_artifact_group(
        self,
        *,
        plan: Plan,
        write_step_indexes: list[int],
        needles: tuple[str, ...],
    ) -> bool:
        return any(
            self._artifact_matches(artifact_id, needles)
            for index in write_step_indexes
            for artifact_id in plan.steps[index].input_artifacts
        )

    def _artifact_matches(self, artifact_id: str, needles: tuple[str, ...]) -> bool:
        normalized = self._normalize(artifact_id)
        return any(needle in normalized for needle in needles)

    def _metadata_list(self, plan: Plan, key: str) -> list:
        value = plan.metadata.get(key)
        return value if isinstance(value, list) and value else []

    def _validate_phase_progression(self, *, envelope: Envelope, plan: Plan, errors: list[str]) -> None:
        phase_steps: list[tuple[int, str, bool]] = []
        for index, step in enumerate(plan.steps):
            phase = step.phase or ""
            write_files = bool(step.permissions.get("write_files", False))
            phase_steps.append((index, phase, write_files))

        previous_phase_index = -1
        finalize_seen = False
        first_mutate_index: int | None = None
        last_mutate_index: int | None = None

        for index, phase, write_files in phase_steps:
            phase_index = PHASE_INDEX.get(phase)
            if phase_index is None:
                continue

            if phase_index < previous_phase_index:
                errors.append(f"plan phase order regresses at step index {index}")
            previous_phase_index = phase_index

            if finalize_seen:
                errors.append("plan has steps after FINALIZE")
                break

            if phase == "FINALIZE":
                finalize_seen = True

            if phase == "MUTATE" or write_files:
                if first_mutate_index is None:
                    first_mutate_index = index
                last_mutate_index = index

        if first_mutate_index is None:
            return

        if envelope.confidence < 0.7:
            has_pre_discovery = any(
                phase in {"DISCOVER", "ANALYZE", "RESEARCH", "DESIGN"}
                for step_index, phase, _ in phase_steps
                if step_index < first_mutate_index
            )
            if not has_pre_discovery:
                errors.append("plan cannot start mutation before discovery under low confidence")

        if self._requires_discovery_before_mutation(envelope):
            has_pre_discovery = any(
                phase in {"DISCOVER", "ANALYZE", "RESEARCH", "DESIGN"}
                for step_index, phase, _ in phase_steps
                if step_index < first_mutate_index
            )
            if not has_pre_discovery:
                errors.append("plan requires discovery/analysis before mutation")

        has_post_verify = any(
            phase == "VERIFY"
            for step_index, phase, _ in phase_steps
            if last_mutate_index is not None and step_index > last_mutate_index
        )
        if not has_post_verify:
            errors.append("plan requires VERIFY after MUTATE")

        has_post_finalize = any(
            phase == "FINALIZE"
            for step_index, phase, _ in phase_steps
            if last_mutate_index is not None and step_index > last_mutate_index
        )
        if not has_post_finalize:
            errors.append("plan requires FINALIZE after mutation flow")

    def _has_separate_dependency_and_evidence_sources(
        self,
        *,
        plan: Plan,
        write_step_indexes: list[int],
        produced_by: dict[str, int],
        evidence_groups: set[str],
        dependency_needles: tuple[str, ...],
    ) -> bool:
        mutation_inputs = {
            artifact_id
            for index in write_step_indexes
            for artifact_id in plan.steps[index].input_artifacts
        }

        dependency_source_steps = {
            produced_by[artifact_id]
            for artifact_id in mutation_inputs
            if artifact_id in produced_by and self._artifact_matches(artifact_id, dependency_needles)
        }
        evidence_source_steps: set[int] = set()
        for group in evidence_groups:
            group_needles = ("evidence",) if group == "general" else (group, "evidence")
            evidence_source_steps.update(
                produced_by[artifact_id]
                for artifact_id in mutation_inputs
                if artifact_id in produced_by and self._artifact_matches(artifact_id, group_needles)
            )

        if not dependency_source_steps or not evidence_source_steps:
            return True
        return not dependency_source_steps.issubset(evidence_source_steps) or not evidence_source_steps.issubset(
            dependency_source_steps
        )

    def _normalize(self, value: str) -> str:
        return value.lower().replace("-", "_").replace(" ", "_")
