from __future__ import annotations

from typing import Any


class CapabilityRegistry:
    def __init__(self) -> None:
        self._planners: dict[str, object] = {}
        self._mutation_policies: dict[str, object] = {}
        self._mutation_executors: dict[str, object] = {}
        self._verifiers: dict[str, object] = {}
        self._artifact_schemas: dict[str, dict[str, Any]] = {}

    def register_planner(self, capability_id: str, planner: object) -> None:
        self._planners[capability_id] = planner

    def register_mutation_policy(self, capability_id: str, policy: object) -> None:
        self._mutation_policies[capability_id] = policy

    def register_mutation_executor(self, capability_id: str, executor: object) -> None:
        self._mutation_executors[capability_id] = executor

    def register_verifier(self, capability_id: str, verifier: object) -> None:
        self._verifiers[capability_id] = verifier

    def register_artifact_schema(self, schema_id: str, schema: dict[str, Any]) -> None:
        self._artifact_schemas[schema_id] = schema

    def planner(self, capability_id: str) -> object:
        return self._planners[capability_id]

    def mutation_policy(self, capability_id: str) -> object:
        return self._mutation_policies[capability_id]

    def mutation_executor(self, capability_id: str) -> object:
        return self._mutation_executors[capability_id]

    def verifier(self, capability_id: str) -> object:
        return self._verifiers[capability_id]

    def artifact_schema(self, schema_id: str) -> dict[str, Any]:
        return self._artifact_schemas[schema_id]
