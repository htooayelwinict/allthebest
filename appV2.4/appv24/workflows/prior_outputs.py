"""Helpers for replaying prior workflow run outputs into a fixture."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from appv24.workflows.run_store import WorkflowRunStore


def fixture_with_prior_run_outputs(
    fixture: Mapping[str, Any],
    store: WorkflowRunStore,
    approval_run_id: str | None,
) -> dict[str, Any]:
    """Return a fixture augmented with outputs from the approved local run."""

    merged = dict(fixture)
    if not approval_run_id:
        return merged
    outputs: dict[str, dict[str, Any]] = {}
    try:
        manifest = store.read_run_manifest(approval_run_id)
        handoff_run_id = manifest.get("handoff_run_id")
        if isinstance(handoff_run_id, str) and handoff_run_id:
            handoff_trace = json.loads((store.run_dir(handoff_run_id) / "trace.json").read_text(encoding="utf-8"))
            outputs.update(_trace_outputs(handoff_trace if isinstance(handoff_trace, Mapping) else {}))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    try:
        trace_payload = json.loads((store.run_dir(approval_run_id) / "trace.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        trace_payload = {}
    outputs.update(_trace_outputs(trace_payload if isinstance(trace_payload, Mapping) else {}))
    if not outputs:
        return merged
    raw_steps = merged.get("steps")
    steps: dict[str, dict[str, Any]] = {}
    if isinstance(raw_steps, Mapping):
        for step_id, value in raw_steps.items():
            if isinstance(step_id, str) and isinstance(value, Mapping):
                steps[step_id] = {str(key): item for key, item in value.items()}
    for step_id, step_outputs in outputs.items():
        step_entry = dict(steps.get(step_id, {}))
        step_entry["outputs"] = step_outputs
        steps[step_id] = step_entry
    merged["steps"] = steps
    return merged


def _trace_outputs(trace_payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    outputs: dict[str, dict[str, Any]] = {}
    trace = trace_payload.get("trace")
    if not isinstance(trace, list):
        return outputs
    for item in trace:
        if not isinstance(item, Mapping) or item.get("status") != "completed":
            continue
        step_id = item.get("step_id")
        simulated_outputs = item.get("simulated_outputs")
        if isinstance(step_id, str) and isinstance(simulated_outputs, Mapping) and simulated_outputs:
            outputs[step_id] = {str(key): value for key, value in simulated_outputs.items()}
    return outputs
