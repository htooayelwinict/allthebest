from __future__ import annotations

from appv23.ai.types import Model
from appv23.coding_agent.agent_session import AgentSession
from appv23.coding_agent.subagents import CallableSubagentBackend, CodexExecBackend, SubagentSupervisor, SubagentTask


def faux_model() -> Model:
    return Model(
        id="faux/test",
        name="Faux Test",
        api="openai-completions",
        provider="faux",
        base_url="https://example.invalid",
        context_window=1000,
        max_tokens=256,
        reasoning=False,
    )


def test_supervisor_runs_callable_backend_and_records_lifecycle_events(tmp_path):
    events = []
    supervisor = SubagentSupervisor(max_threads=2, event_sink=events.append)
    supervisor.register_backend(CallableSubagentBackend("internal", lambda task: f"done: {task.goal}"))

    task_id = supervisor.spawn(SubagentTask(role="researcher", goal="inspect docs", cwd=str(tmp_path)))
    result = supervisor.wait(task_id, timeout=2)

    assert result.status == "completed"
    assert result.summary == "done: inspect docs"
    assert result.task_id == task_id
    assert [event["type"] for event in events] == ["subagent_start", "subagent_stop"]
    assert events[0]["child_role"] == "researcher"
    assert events[1]["status"] == "completed"


def test_supervisor_rejects_unregistered_backend(tmp_path):
    supervisor = SubagentSupervisor(max_threads=1)

    try:
        supervisor.spawn(SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path), backend="missing"))
    except ValueError as error:
        assert "No subagent backend registered" in str(error)
    else:  # pragma: no cover - assertion path
        raise AssertionError("Expected missing backend to fail")


def test_codex_exec_backend_parses_jsonl_final_agent_message(tmp_path):
    calls = []

    def fake_runner(args, cwd, timeout, text, capture_output):
        calls.append((args, cwd, timeout, text, capture_output))
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": '{"type":"item.completed","item":{"type":"agent_message","text":"final summary"}}\n',
                "stderr": "",
            },
        )()

    backend = CodexExecBackend(runner=fake_runner)
    result = backend.run(SubagentTask(role="codex", goal="review", cwd=str(tmp_path), backend="codex"))

    assert result.status == "completed"
    assert result.summary == "final summary"
    assert calls[0][0][:4] == ["codex", "exec", "--json", "--sandbox"]
    assert "read-only" in calls[0][0]
    assert calls[0][1] == str(tmp_path)


def test_codex_exec_backend_reports_nonzero_exit(tmp_path):
    def fake_runner(args, cwd, timeout, text, capture_output):
        return type("Completed", (), {"returncode": 2, "stdout": "", "stderr": "bad auth"})()

    backend = CodexExecBackend(runner=fake_runner)
    result = backend.run(SubagentTask(role="codex", goal="review", cwd=str(tmp_path), backend="codex"))

    assert result.status == "failed"
    assert result.errors == ["bad auth"]


def test_agent_session_delegate_command_spawns_subagent_and_returns_summary(tmp_path):
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", lambda task: f"summary for {task.goal}"))

    messages = session.prompt("/delegate researcher inspect tests")

    assert any("summary for inspect tests" in getattr(message, "content", "") for message in messages)
    assert session.subagents.list_results()[0].role == "researcher"


def test_agent_session_agents_command_lists_completed_subagents(tmp_path):
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", lambda task: "done"))
    session.prompt("/delegate reviewer scan code")

    messages = session.prompt("/agents")

    rendered = "\n".join(str(getattr(message, "content", "")) for message in messages)
    assert "reviewer" in rendered
    assert "completed" in rendered
