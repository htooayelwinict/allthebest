"""State reducer for AppV2.1 runtime events."""

from __future__ import annotations

from appv21.state.events import RuntimeEvent
from appv21.state.models import AgentState, Artifact, MutationLease, MutationReceipt, PauseState, PlanState, WorldRef


def reduce_event(state: AgentState, event: RuntimeEvent) -> AgentState:
    payload = event.payload
    event_type = event.event_type

    if event_type == "ModeChanged":
        state.mode = payload["mode"]
    elif event_type == "UserMessageReceived":
        state.conversation.messages.append({"role": "user", "content": payload["content"]})
    elif event_type == "WorldRefAdded":
        ref = WorldRef(**payload)
        state.world.refs[ref.ref_id] = ref
    elif event_type == "PlanAccepted":
        state.plan = PlanState(**payload)
    elif event_type == "PauseRequested":
        state.pauses.append(PauseState(**payload))
        state.mode = "PAUSE"
    elif event_type == "RunPaused":
        state.mode = "PAUSE"
        state.terminal = True
        state.result = payload
    elif event_type == "PauseResolved":
        state.terminal = False
        state.result = None
    elif event_type == "RunResumed":
        state.mode = payload.get("mode", "THINK")
        state.terminal = False
        state.result = None
    elif event_type == "ArtifactAccepted":
        artifact = Artifact(**payload)
        state.world.artifacts[artifact.artifact_id] = artifact
    elif event_type == "MutationLeaseIssued":
        lease = MutationLease(**payload)
        state.world.mutation_leases[lease.lease_id] = lease
    elif event_type == "MutationApplied":
        receipt = MutationReceipt(**payload)
        state.world.mutation_receipts[receipt.receipt_id] = receipt
    elif event_type == "VerificationRecorded":
        state.world.verification_receipts[payload["verification_id"]] = payload
    elif event_type == "ContextCompacted":
        state.context.compacted_turns += 1
        state.context.world_digest = payload.get("world_digest", {})
        state.context.conversation_digest = payload.get("conversation_digest", "")
    elif event_type == "RunCompleted":
        state.mode = "FINALIZE"
        state.terminal = True
        state.result = payload
    elif event_type == "RunFailed":
        state.mode = "FAILED"
        state.terminal = True
        state.result = payload
    return state


def reduce_events(state: AgentState, events: list[RuntimeEvent]) -> AgentState:
    for event in events:
        state = reduce_event(state, event)
    return state
