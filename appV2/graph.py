"""LangGraph assembly for AppV2 runtimes."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from appV2.decomposer.runtime import DecomposerRuntime
from appV2.planner.runtime import PhasePlannerRuntime
from appV2.runtime_matrix import coerce_runtime_matrix
from appV2.schemas import Envelope, PhasePlan, RuntimeState
from appV2.worker.runtime import WorkerRuntime


def build_graph(
    *,
    decomposer_runtime=None,
    planner_runtime=None,
    worker_runtime=None,
    client_factory=None,
    planner_client_factory=None,
    worker_client_factory=None,
    root_path: str = ".",
):
    if decomposer_runtime is None:
        client_options = {"client_factory": client_factory} if client_factory is not None else {}
        decomposer_runtime = DecomposerRuntime.from_env(**client_options)
    if planner_runtime is None:
        planner_options = {"client_factory": planner_client_factory} if planner_client_factory is not None else {}
        planner_runtime = PhasePlannerRuntime.from_env(**planner_options)
    if worker_runtime is None:
        worker_options = {"client_factory": worker_client_factory} if worker_client_factory is not None else {}
        worker_runtime = WorkerRuntime.from_env(
            planner_runtime=planner_runtime,
            root_path=root_path,
            **worker_options,
        )

    def decomposer_node(state: RuntimeState) -> RuntimeState:
        trace = coerce_runtime_matrix(None, state.get("runtime_matrix"))
        envelope = decomposer_runtime.run(state.get("user_input", ""), trace=trace)
        return {"envelope": envelope.model_dump(mode="json"), "runtime_matrix": trace.snapshot(), "errors": state.get("errors", [])}

    def planner_node(state: RuntimeState) -> RuntimeState:
        trace = coerce_runtime_matrix(None, state.get("runtime_matrix"))
        envelope = Envelope.model_validate(state["envelope"])
        plan = planner_runtime.run(envelope, trace=trace)
        return {"phase_plan": plan.model_dump(mode="json"), "runtime_matrix": trace.snapshot(), "errors": state.get("errors", [])}

    def worker_node(state: RuntimeState) -> RuntimeState:
        trace = coerce_runtime_matrix(None, state.get("runtime_matrix"))
        envelope = Envelope.model_validate(state["envelope"])
        plan = PhasePlan.model_validate(state["phase_plan"])
        result = worker_runtime.run(plan, envelope=envelope, trace=trace)
        return {"result": result.model_dump(mode="json"), "runtime_matrix": trace.snapshot(), "errors": state.get("errors", [])}

    graph = StateGraph(RuntimeState)
    graph.add_node("decomposer_node", decomposer_node)
    graph.add_node("phase_planner_node", planner_node)
    graph.add_node("worker_runtime_node", worker_node)
    graph.add_edge(START, "decomposer_node")
    graph.add_edge("decomposer_node", "phase_planner_node")
    graph.add_edge("phase_planner_node", "worker_runtime_node")
    graph.add_edge("worker_runtime_node", END)
    return graph.compile()
