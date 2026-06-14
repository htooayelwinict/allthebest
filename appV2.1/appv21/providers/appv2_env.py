"""AppV2-compatible live provider for AppV2.1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from appv21.providers.base import AgentProvider
from appv21.providers.env_config import build_appv21_model_client
from appv21.providers.model_client import AppV21JSONClient
from appv21.providers.null_model import NullModelProvider
from appv21.runtime.decisions import RuntimeDecision, finalize_decision, mutation_decision, parse_runtime_decision, plan_decision, verify_decision


DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision_id": {"type": "string"},
        "kind": {
            "type": "string",
            "enum": ["observe", "read_file", "plan", "tool_call", "mutation_intent", "verify", "pause", "compact", "finalize"],
        },
        "reason": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "payload": {"type": "object", "additionalProperties": True},
    },
    "required": ["kind", "reason", "evidence_refs", "payload"],
}


class AppV2EnvAgentProvider:
    """Provider that reuses AppV2's APPV2_WORKER_LLM_* environment contract."""

    provider_id = "appv2-env-worker"

    def __init__(self, *, client: Any) -> None:
        self.client = client

    def decide(self, prompt_payload: dict) -> RuntimeDecision:
        raw = self.client.complete_json(
            stage="appv21_decision",
            prompt=_decision_prompt(prompt_payload),
            schema=DECISION_SCHEMA,
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return RuntimeDecision(
                kind="pause",
                reason="Model returned invalid JSON for AppV2.1 decision.",
                payload={"pause_type": "tool_blocked", "raw_preview": raw[:500]},
            )
        if not isinstance(payload, dict):
            return RuntimeDecision(kind="pause", reason="Model decision was not a JSON object.", payload={"pause_type": "tool_blocked"})
        decision = parse_runtime_decision(payload)
        decision = _normalize_common_tool_payload(decision)
        redundant_ref = _redundant_repo_snapshot_ref(prompt_payload, decision)
        if redundant_ref is not None:
            return plan_decision(evidence_refs=[redundant_ref], reason="Repo snapshot already exists; proceed to planning.")
        tool_progressed = _coerce_redundant_tool_to_next_runtime_step(prompt_payload, decision)
        if tool_progressed is not None:
            return tool_progressed
        progressed = _coerce_redundant_plan_to_next_runtime_step(prompt_payload, decision)
        if progressed is not None:
            return progressed
        return decision


def create_appv21_provider_from_appv2_env(
    *,
    dotenv_path: str | Path = ".env",
    client_factory: type[Any] = AppV21JSONClient,
) -> AgentProvider:
    client = build_appv21_model_client("APPV2_WORKER_LLM", dotenv_path, client_factory=client_factory)
    if client is None:
        return NullModelProvider()
    return AppV2EnvAgentProvider(client=client)


def _decision_prompt(prompt_payload: dict) -> str:
    return "\n".join(
        [
            "You are selecting the next AppV2.1 runtime decision.",
            "Return only JSON matching the supplied schema.",
            "Decisions are proposals. The runtime validates evidence, tools, mutation leases, and finalization.",
            "If world_refs already contains kind=repo_snapshot, do not call repo_snapshot again; choose plan with that ref.",
            "Do not claim files were changed unless the runtime has mutation or verification evidence.",
            json.dumps(prompt_payload, indent=2, sort_keys=True, default=str),
        ]
    )


def _redundant_repo_snapshot_ref(prompt_payload: dict, decision: RuntimeDecision) -> str | None:
    if decision.kind == "observe":
        tool_name = "repo_snapshot"
    elif decision.kind == "tool_call":
        tool_name = str(decision.payload.get("tool_name") or decision.payload.get("tool") or "")
    else:
        return None
    if tool_name != "repo_snapshot":
        return None
    for ref in prompt_payload.get("world", {}).get("world_refs", []):
        if ref.get("kind") == "repo_snapshot":
            return str(ref.get("ref_id") or "world://repo_snapshot/latest")
    return None


def _normalize_common_tool_payload(decision: RuntimeDecision) -> RuntimeDecision:
    if decision.kind != "tool_call":
        return decision
    payload = dict(decision.payload)
    if "tool_name" not in payload and "tool" in payload:
        payload["tool_name"] = payload["tool"]
    if "arguments" not in payload and "params" in payload:
        payload["arguments"] = payload["params"]
    return RuntimeDecision(
        kind=decision.kind,
        reason=decision.reason,
        payload=payload,
        evidence_refs=decision.evidence_refs,
        decision_id=decision.decision_id,
    )


def _coerce_redundant_plan_to_next_runtime_step(prompt_payload: dict, decision: RuntimeDecision) -> RuntimeDecision | None:
    if decision.kind != "plan":
        return None
    state = prompt_payload.get("state", {})
    plan = state.get("plan")
    if not isinstance(plan, dict):
        return None
    runtime_plan = plan.get("runtime_plan") if isinstance(plan.get("runtime_plan"), dict) else plan
    if not state.get("mutation_receipts"):
        return mutation_decision(plan=runtime_plan, reason="Plan already exists; advance to mutation intent.")
    if not state.get("verification_receipts"):
        return verify_decision(plan=runtime_plan, reason="Mutation receipt exists; advance to verification.")
    if not state.get("artifacts"):
        return finalize_decision(reason="Verification exists; advance to finalization.")
    return None


def _coerce_redundant_tool_to_next_runtime_step(prompt_payload: dict, decision: RuntimeDecision) -> RuntimeDecision | None:
    if decision.kind != "tool_call":
        return None
    tool_name = str(decision.payload.get("tool_name") or "")
    if tool_name not in {"derive_mutation_lease", "apply_mutation_lease"}:
        return None
    state = prompt_payload.get("state", {})
    plan = state.get("plan")
    if not isinstance(plan, dict):
        return None
    runtime_plan = plan.get("runtime_plan") if isinstance(plan.get("runtime_plan"), dict) else plan
    if not state.get("mutation_receipts"):
        return mutation_decision(plan=runtime_plan, reason=f"Model requested {tool_name}; runtime converts this to mutation intent.")
    if not state.get("verification_receipts"):
        return verify_decision(plan=runtime_plan, reason=f"Model requested {tool_name}, but mutation is already applied; advance to verification.")
    if not state.get("artifacts"):
        return finalize_decision(reason=f"Model requested {tool_name}, but verification exists; advance to finalization.")
    return None
