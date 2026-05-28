from app.graph import build_graph


def test_compiled_graph_invocation(monkeypatch) -> None:
    monkeypatch.setenv("DECOMPRESSOR_LLM_ENABLED", "false")
    graph = build_graph()

    state = graph.invoke({"user_input": "what is docker", "errors": []})

    assert "envelope" in state
    assert "plan" in state
    assert "result" in state
    assert state["result"]["status"] == "completed"
    assert "llm_prompt_chain" not in state["envelope"].get("metadata", {})


def test_graph_registers_required_node_keys_when_exposed() -> None:
    graph = build_graph()

    nodes = getattr(graph, "nodes", None)
    if isinstance(nodes, dict):
        assert "decompressor_node" in nodes
        assert "planner_node" in nodes
        assert "worker_kernel_node" in nodes
