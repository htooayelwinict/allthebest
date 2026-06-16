from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "appV2.2"))

from appv22.runtime.decisions import RuntimeDecision
from appv22.runtime.reducer import DEFAULT_REDUCER, ReducerRegistry, apply_event
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
    decision = RuntimeDecision(kind="tool_call", reason="use active extension tool")
    event = RuntimeEvent("DecisionProposed", decision.to_dict())

    assert event.payload["kind"] == "tool_call"
    assert event.event_type == "DecisionProposed"


def test_runtime_decision_rejects_plan_kind():
    with pytest.raises(ValueError, match="Unknown runtime decision kind"):
        RuntimeDecision(kind="plan", reason="separate planning lane must not exist")


def test_runtime_decision_to_dict_does_not_expose_mutable_fields():
    decision = RuntimeDecision(
        kind="tool_call",
        reason="keep records immutable",
        payload={"steps": ["one"], "nested": {"ok": True}},
        evidence_refs=["ref-1"],
    )

    serialized = decision.to_dict()
    serialized["payload"]["steps"].append("two")
    serialized["payload"]["nested"]["ok"] = False
    serialized["evidence_refs"].append("ref-2")

    assert decision.payload == {"steps": ["one"], "nested": {"ok": True}}
    assert decision.evidence_refs == ["ref-1"]


def test_runtime_event_to_dict_does_not_expose_mutable_payload():
    event = RuntimeEvent(
        "DecisionProposed",
        {"decision": {"kind": "tool_call"}, "evidence_refs": ["ref-1"]},
    )

    serialized = event.to_dict()
    serialized["payload"]["decision"]["kind"] = "verify"
    serialized["payload"]["evidence_refs"].append("ref-2")

    assert event.payload == {"decision": {"kind": "tool_call"}, "evidence_refs": ["ref-1"]}


def test_runtime_decision_rejects_unknown_kind():
    with pytest.raises(ValueError, match="Unknown runtime decision kind"):
        RuntimeDecision(kind="not-a-kind", reason="reject ambiguous records")


def test_reducer_exposes_extensible_handler_registry():
    assert DEFAULT_REDUCER.has_handler("ModeChanged")
    assert DEFAULT_REDUCER.has_handler("ContextSummaryUpdated")


def test_reducer_registry_accepts_extension_owned_handlers_without_core_artifact_state():
    class ExtensionRecordedHandler:
        event_type = "ExtensionRecorded"

        def apply(self, state, payload):
            if not hasattr(state, "extension_records"):
                state.extension_records = {}
            state.extension_records[payload["record_id"]] = payload

    state = AgentState(session_id="sess", run_id="run", request=RequestEnvelope("req", "clean this", "."))
    reducer = ReducerRegistry([ExtensionRecordedHandler()])

    reducer.apply(state, RuntimeEvent("ExtensionRecorded", {"record_id": "record_1", "kind": "report"}))

    assert state.extension_records["record_1"] == {"record_id": "record_1", "kind": "report"}


def test_world_ref_added_updates_durable_context_summary_evidence_refs():
    state = AgentState(session_id="sess", run_id="run", request=RequestEnvelope("req", "observe", "."))

    apply_event(
        state,
        RuntimeEvent(
            "WorldRefAdded",
            {
                "ref_id": "world://repo_snapshot/latest",
                "kind": "file_management.repo_snapshot",
                "summary": "file_management.repo_snapshot result",
                "payload": {"files": ["docs/context.md"]},
            },
        ),
    )

    assert state.world_refs["world://repo_snapshot/latest"]["kind"] == "file_management.repo_snapshot"
    assert state.context_summary["evidence_refs"] == ["world://repo_snapshot/latest"]
    assert state.context_summary["progress"] == [
        "file_management.repo_snapshot: file_management.repo_snapshot result"
    ]
