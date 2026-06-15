import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.2"))

from appv22.tools.broker import ToolBroker
from appv22.tools.definitions import ToolDefinition
from appv22.tools.registry import ToolRegistry


def _echo_definition(argument_schema=None):
    return ToolDefinition(
        "demo.echo",
        "observe",
        "low",
        argument_schema or {"required": ["message"]},
        {"type": "object"},
        "runtime_observed",
        "Echo.",
    )


def test_tool_broker_executes_registered_active_tool(tmp_path):
    registry = ToolRegistry()
    registry.register(
        _echo_definition(),
        lambda args, _ctx: {"status": "completed", "message": args["message"]},
    )
    broker = ToolBroker(registry=registry, root_path=tmp_path)

    result = broker.execute("demo.echo", {"message": "hello"}, active_tool_ids=["demo.echo"])

    assert result["tool_id"] == "demo.echo"
    assert result["tool_result_id"].startswith("toolres_")
    assert result["status"] == "completed"
    assert result["payload"] == {"message": "hello"}
    assert result["payload_ref"].startswith("world://tool_payload/")
    assert result["evidence_refs"] == []


def test_tool_broker_denies_missing_required_argument(tmp_path):
    registry = ToolRegistry()
    registry.register(
        _echo_definition(),
        lambda args, _ctx: {"status": "completed", "message": args["message"]},
    )
    broker = ToolBroker(registry=registry, root_path=tmp_path)

    result = broker.execute("demo.echo", {}, active_tool_ids=["demo.echo"])

    assert result["status"] == "denied"
    assert result["payload"] == {"errors": ["missing_argument:message"]}
    assert result["payload_ref"] == ""


def test_tool_broker_denies_unknown_active_tool(tmp_path):
    broker = ToolBroker(registry=ToolRegistry(), root_path=tmp_path)

    result = broker.execute("demo.missing", {}, active_tool_ids=["demo.missing"])

    assert result["tool_id"] == "demo.missing"
    assert result["status"] == "denied"
    assert result["payload"] == {"errors": ["unknown_tool:demo.missing"]}
    assert result["payload_ref"] == ""


def test_tool_broker_denies_registered_but_inactive_tool(tmp_path):
    registry = ToolRegistry()
    registry.register(_echo_definition(), lambda _args, _ctx: {"status": "completed"})
    broker = ToolBroker(registry=registry, root_path=tmp_path)

    result = broker.execute("demo.echo", {"message": "hello"}, active_tool_ids=[])

    assert result["status"] == "denied"
    assert result["payload"] == {"errors": ["inactive_tool:demo.echo"]}
    assert result["payload_ref"] == ""


def test_tool_definition_freezes_schema_against_external_mutation():
    argument_schema = {"required": ["message"]}
    definition = _echo_definition(argument_schema)

    argument_schema["required"].append("mutated")

    assert definition.argument_schema["required"] == ("message",)
    with pytest.raises(TypeError):
        definition.argument_schema["extra"] = True


def test_registry_rejects_duplicate_tool_ids():
    registry = ToolRegistry()
    registry.register(_echo_definition(), lambda _args, _ctx: {"status": "completed"})

    with pytest.raises(ValueError, match="duplicate tool_id: demo.echo"):
        registry.register(_echo_definition(), lambda _args, _ctx: {"status": "completed"})


def test_broker_does_not_mutate_arguments_or_handler_result_payload(tmp_path):
    registry = ToolRegistry()
    arguments = {"message": "hello", "items": ["original"]}
    handler_result = {"status": "completed", "items": ["handler"]}

    def handler(args, _ctx):
        args["items"].append("handler-mutated-arg-copy")
        return handler_result

    registry.register(_echo_definition({"required": ["message", "items"]}), handler)
    broker = ToolBroker(registry=registry, root_path=tmp_path)

    result = broker.execute("demo.echo", arguments, active_tool_ids=["demo.echo"])
    result["payload"]["items"].append("result-mutated-copy")

    assert arguments == {"message": "hello", "items": ["original"]}
    assert handler_result == {"status": "completed", "items": ["handler"]}
