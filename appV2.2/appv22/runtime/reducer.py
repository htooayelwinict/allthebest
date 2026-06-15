from __future__ import annotations


def apply_event(state, event) -> None:
    payload = event.payload
    if event.event_type == "ModeChanged":
        state.mode = payload["mode"]
    elif event.event_type == "WorldRefAdded":
        state.world_refs[payload["ref_id"]] = payload
    elif event.event_type == "ToolCallCompleted":
        state.tool_results[payload["tool_result_id"]] = payload
    elif event.event_type == "ToolCallDenied":
        state.tool_results[payload["tool_result_id"]] = payload
    elif event.event_type == "PlanAccepted":
        state.runtime_plan = payload
    elif event.event_type == "MutationLeaseIssued":
        state.mutation_leases[payload["lease_id"]] = payload
    elif event.event_type == "MutationApplied":
        state.mutation_receipts[payload["receipt_id"]] = payload
    elif event.event_type == "VerificationRecorded":
        state.verification_receipts[payload["verification_id"]] = payload
    elif event.event_type == "ContextSummaryUpdated":
        state.context_summary = payload
    elif event.event_type == "RunCompleted":
        state.terminal = True
        state.mode = "FINALIZE"
        state.result = payload
    elif event.event_type == "RunFailed":
        state.terminal = True
        state.mode = "FAILED"
        state.result = payload
