from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "appV2.2"))

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
