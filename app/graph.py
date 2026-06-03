"""LangGraph assembly for Phase 1 runtime architecture."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.decompressor.runtime import DecompressorRuntime
from app.planner.runtime import PlannerRuntime
from app.runtime_matrix import RuntimeMatrixLogger, coerce_runtime_matrix
from app.schemas import Envelope, Plan, RuntimeState
from app.worker_kernel.runtime import WorkerKernelRuntime


def build_graph(
    *,
    decompressor_runtime=None,
    planner_runtime=None,
    worker_kernel_runtime=None,
    client_factory=None,
    planner_client_factory=None,
    worker_client_factory=None,
):
    if decompressor_runtime is None:
        client_options = {"client_factory": client_factory} if client_factory is not None else {}
        decompressor_runtime = DecompressorRuntime.from_env(**client_options)
    if planner_runtime is None:
        planner_options = (
            {"client_factory": planner_client_factory} if planner_client_factory is not None else {}
        )
        planner_runtime = PlannerRuntime.from_env(**planner_options)
    if worker_kernel_runtime is None:
        worker_options = {"client_factory": worker_client_factory} if worker_client_factory is not None else {}
        worker_kernel_runtime = WorkerKernelRuntime.from_env(
            planner_runtime=planner_runtime,
            **worker_options,
        )

    def decompressor_node(state: RuntimeState) -> RuntimeState:
        trace = coerce_runtime_matrix(None, state.get("runtime_matrix"))
        user_input = state.get("user_input", "")
        trace.record(
            component="graph",
            stage="decompressor_node",
            event="node_enter",
            status="started",
            details={"input_chars": len(user_input or "")},
        )
        try:
            envelope = decompressor_runtime.run(user_input, trace=trace)
        except Exception as exc:
            trace.record(
                component="graph",
                stage="decompressor_node",
                event="node_failed",
                status="failed",
                details={"error": str(exc)},
            )
            raise
        trace.record(
            component="graph",
            stage="decompressor_node",
            event="node_exit",
            status="completed",
            request_id=envelope.request_id,
        )
        return {
            "envelope": envelope.model_dump(),
            "runtime_matrix": trace.snapshot(),
            "errors": state.get("errors", []),
        }

    def planner_node(state: RuntimeState) -> RuntimeState:
        trace = coerce_runtime_matrix(None, state.get("runtime_matrix"))
        envelope = Envelope.model_validate(state["envelope"])
        trace.record(
            component="graph",
            stage="planner_node",
            event="node_enter",
            status="started",
            request_id=envelope.request_id,
        )
        try:
            plan = planner_runtime.run(envelope, trace=trace)
        except Exception as exc:
            trace.record(
                component="graph",
                stage="planner_node",
                event="node_failed",
                status="failed",
                request_id=envelope.request_id,
                details={"error": str(exc)},
            )
            raise
        trace.record(
            component="graph",
            stage="planner_node",
            event="node_exit",
            status="completed",
            request_id=envelope.request_id,
            plan_id=plan.plan_id,
        )
        return {
            "plan": plan.model_dump(),
            "runtime_matrix": trace.snapshot(),
            "errors": state.get("errors", []),
        }

    def worker_kernel_node(state: RuntimeState) -> RuntimeState:
        trace = coerce_runtime_matrix(None, state.get("runtime_matrix"))
        envelope = Envelope.model_validate(state["envelope"])
        plan = Plan.model_validate(state["plan"])
        trace.record(
            component="graph",
            stage="worker_kernel_node",
            event="node_enter",
            status="started",
            request_id=envelope.request_id,
            plan_id=plan.plan_id,
        )
        try:
            result = worker_kernel_runtime.run(plan, envelope=envelope, trace=trace)
        except Exception as exc:
            trace.record(
                component="graph",
                stage="worker_kernel_node",
                event="node_failed",
                status="failed",
                request_id=envelope.request_id,
                plan_id=plan.plan_id,
                details={"error": str(exc)},
            )
            raise
        trace.record(
            component="graph",
            stage="worker_kernel_node",
            event="node_exit",
            status=result.status,
            request_id=envelope.request_id,
            plan_id=plan.plan_id,
            run_id=result.run_id,
        )
        return {
            "result": result.model_dump(),
            "runtime_matrix": trace.snapshot(),
            "errors": state.get("errors", []),
        }

    graph = StateGraph(RuntimeState)
    graph.add_node("decompressor_node", decompressor_node)
    graph.add_node("planner_node", planner_node)
    graph.add_node("worker_kernel_node", worker_kernel_node)

    graph.add_edge(START, "decompressor_node")
    graph.add_edge("decompressor_node", "planner_node")
    graph.add_edge("planner_node", "worker_kernel_node")
    graph.add_edge("worker_kernel_node", END)

    return graph.compile()
