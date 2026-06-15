import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.2"))

from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.extensions.file_management.mutation_executor import FileMutationExecutor
from appv22.extensions.file_management.mutation_policy import FileMoveMutationPolicy
from appv22.extensions.file_management.planner import FileCleanupPlanner
from appv22.extensions.file_management.verifier import WorkspaceManifestVerifier
from appv22.extensions.registry import ExtensionRegistry
from appv22.runtime.capabilities import CapabilityRegistry
from appv22.state.models import AgentState, RequestEnvelope
from appv22.tools.registry import ToolRegistry


def test_file_management_extension_registers_all_capabilities():
    extension = FileManagementExtension()
    registry = ExtensionRegistry()
    capabilities = CapabilityRegistry()
    registry.register(extension)
    extension.register_capabilities(capabilities)
    state = AgentState("sess", "run", RequestEnvelope("req", "tidy this workspace mess", "."))

    resolved = registry.resolve_active(state)

    assert resolved.extension_ids == ("file_management",)
    assert resolved.planner_ids == ("file_management.cleanup_planner",)
    assert resolved.mutation_policy_ids == ("file_management.safe_file_moves",)
    assert resolved.mutation_executor_ids == ("file_management.file_mutation_executor",)
    assert resolved.verifier_ids == ("file_management.manifest_verifier",)
    assert resolved.tool_ids == ("file_management.read_file", "file_management.repo_snapshot")
    assert resolved.artifact_schema_ids == ("file_management.workspace_manifest",)
    assert capabilities.planner("file_management.cleanup_planner")
    assert capabilities.mutation_policy("file_management.safe_file_moves")
    assert capabilities.mutation_executor("file_management.file_mutation_executor")
    assert capabilities.verifier("file_management.manifest_verifier")
    assert capabilities.artifact_schema("file_management.workspace_manifest")["required"] == [
        "generated_by",
        "moves",
        "held",
        "collisions",
    ]


def test_file_management_skill_activation_handles_vague_prompts():
    extension = FileManagementExtension()
    state = AgentState("sess", "run", RequestEnvelope("req", "make this workspace sane and keep a record", "."))

    assert extension.skill_cards()[0].activates_for(state) is True


def test_file_management_extension_registers_snapshot_and_read_tools(tmp_path):
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/data.json").write_text("{}", encoding="utf-8")
    registry = ToolRegistry()
    FileManagementExtension().register_tools(registry)

    snapshot = registry.handler("file_management.repo_snapshot")({}, {"root_path": tmp_path})
    read = registry.handler("file_management.read_file")({"path": "notes.md"}, {"root_path": tmp_path})

    assert registry.definition("file_management.repo_snapshot").tool_id == "file_management.repo_snapshot"
    assert registry.definition("file_management.read_file").tool_id == "file_management.read_file"
    assert snapshot["status"] == "completed"
    assert snapshot["files"] == ["nested/data.json", "notes.md"]
    assert snapshot["directories"] == ["nested"]
    assert read == {"status": "completed", "path": "notes.md", "content": "hello"}


def test_policy_rejects_root_escape_and_protected_paths(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/existing.md").write_text("existing", encoding="utf-8")
    operations = [
        {"action": "move", "source": "../outside.md", "destination": "docs/outside.md"},
        {"action": "move", "source": "README.md", "destination": "docs/readme.md"},
        {"action": "move", "source": "draft.md", "destination": "docs/existing.md"},
        {"action": "write", "path": "notes/manifest.json", "content": "{}"},
    ]

    errors = FileMoveMutationPolicy().validate(operations, root_path=tmp_path)

    assert "path_outside_root:../outside.md->docs/outside.md" in errors
    assert "protected_source_path:README.md" in errors
    assert "destination_exists:docs/existing.md" in errors
    assert "unsupported_write_path:notes/manifest.json" in errors


def test_planner_holds_moves_when_destination_collides():
    state = AgentState("sess", "run", RequestEnvelope("req", "cleanup", "/workspace"))
    state.world_refs["world://repo_snapshot/latest"] = {
        "payload": {"files": ["draft.md", "docs/draft.md", "logs/run.json", "run.json"]}
    }

    plan = FileCleanupPlanner().plan(state)

    assert plan["mutation_intent"]["operation_batch_id"] == "workspace_cleanup"
    assert {move["source"] for move in plan["verification_intent"]["moves"]} == {"logs/run.json"}
    assert sorted(plan["verification_intent"]["held"]) == ["draft.md", "run.json"]
    collisions = plan["verification_intent"]["collisions"]
    assert {collision["source"] for collision in collisions} == {"draft.md", "run.json"}


def test_executor_applies_validated_moves_and_manifest(tmp_path):
    (tmp_path / "draft.md").write_text("draft", encoding="utf-8")
    operations = [
        {"action": "move", "source": "draft.md", "destination": "docs/draft.md"},
        {
            "action": "write",
            "path": "docs/workspace_manifest.json",
            "content": {"generated_by": "appv22", "moves": [], "held": [], "collisions": []},
        },
    ]
    assert FileMoveMutationPolicy().validate(operations, root_path=tmp_path) == []

    result = FileMutationExecutor().apply(operations, root_path=tmp_path)

    assert result == {
        "status": "applied",
        "touched_paths": ["docs/draft.md", "docs/workspace_manifest.json", "draft.md"],
        "errors": [],
    }
    assert not (tmp_path / "draft.md").exists()
    assert (tmp_path / "docs/draft.md").read_text(encoding="utf-8") == "draft"
    assert json.loads((tmp_path / "docs/workspace_manifest.json").read_text(encoding="utf-8"))["generated_by"] == "appv22"


def test_manifest_verifier_checks_required_manifest_fields(tmp_path):
    manifest_path = tmp_path / "docs/workspace_manifest.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        json.dumps({"generated_by": "appv22", "moves": [], "held": [], "collisions": []}),
        encoding="utf-8",
    )

    result = WorkspaceManifestVerifier().verify(
        root_path=tmp_path,
        verification_intent={"manifest_path": "docs/workspace_manifest.json"},
    )

    assert result["status"] == "passed"
    assert all(check["passed"] for check in result["checks"])
    assert result["manifest"]["generated_by"] == "appv22"
