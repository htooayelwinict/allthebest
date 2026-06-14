from pathlib import Path

from appV2.schemas import FileOperation, MutationPolicy, PhaseStep
from appV2.worker.ledgers import ArtifactLedger, MutationLedger, MutationRecord, snapshot_postimages, snapshot_preimages
from appV2.worker.tools import ToolRegistry


def _mutate_phase(**policy_overrides) -> PhaseStep:
    return PhaseStep(
        phase_id="mutate",
        phase="MUTATE",
        goal="mutate files",
        output_artifacts=["change_summary"],
        allowed_tool_groups=["repo_read", "file_write"],
        mutation_policy=MutationPolicy(**policy_overrides),
    )


def test_tool_registry_writes_inside_repo(tmp_path: Path) -> None:
    phase = _mutate_phase(mode="advisory", max_files=2)
    tools = ToolRegistry(root_path=tmp_path)

    result = tools.execute(
        phase=phase,
        tool_name="write_file",
        arguments={"path": "docs/report.md", "content": "hello"},
    )

    assert result["status"] == "completed"
    assert (tmp_path / "docs/report.md").read_text(encoding="utf-8") == "hello"


def test_tool_registry_exposes_related_quality_tools_without_retry_memory_tool(tmp_path: Path) -> None:
    phase = PhaseStep(
        phase_id="discover",
        phase="DISCOVER",
        goal="inspect",
        allowed_tool_groups=["repo_read"],
    )

    names = {tool["name"] for tool in ToolRegistry(root_path=tmp_path).available_tools(phase)}

    assert {"repo_snapshot", "read_many_files", "diff_summary", "classify_file_management_candidates"} <= names
    assert "resume_from_kernel_memory" not in names


def test_tool_registry_denies_escaping_write_without_mutation(tmp_path: Path) -> None:
    phase = _mutate_phase(mode="advisory", max_files=2)
    tools = ToolRegistry(root_path=tmp_path)

    result = tools.execute(
        phase=phase,
        tool_name="write_file",
        arguments={"path": "../outside.md", "content": "nope"},
    )

    assert result["status"] == "denied"
    assert result["code"] == "path_outside_repo"
    assert not (tmp_path.parent / "outside.md").exists()


def test_tool_registry_strict_policy_denies_unapproved_path(tmp_path: Path) -> None:
    phase = _mutate_phase(mode="strict", allowed_paths=["docs/allowed.md"], max_files=2)
    tools = ToolRegistry(root_path=tmp_path)

    result = tools.execute(
        phase=phase,
        tool_name="write_file",
        arguments={"path": "docs/other.md", "content": "nope"},
    )

    assert result["status"] == "denied"
    assert result["code"] == "path_not_in_strict_policy"
    assert not (tmp_path / "docs/other.md").exists()


def test_tool_registry_write_json_manifest_returns_repairable_denial_for_bad_counts(tmp_path: Path) -> None:
    phase = _mutate_phase(mode="advisory", max_files=2)
    tools = ToolRegistry(root_path=tmp_path)

    result = tools.execute(
        phase=phase,
        tool_name="write_json_manifest",
        arguments={
            "path": "reports/index.json",
            "payload": {"moved_documents": ["a.md"], "total_artifacts": 2},
            "required_keys": ["moved_documents", "total_artifacts"],
            "total_key": "total_artifacts",
            "count_keys": ["moved_documents"],
        },
    )

    assert result["status"] == "denied"
    assert result["code"] == "manifest_total_mismatch"
    assert result["repairable"] is True
    assert not (tmp_path / "reports/index.json").exists()


def test_artifact_and_mutation_ledgers_record_diff(tmp_path: Path) -> None:
    path = tmp_path / "src/app.py"
    path.parent.mkdir()
    path.write_text("old\n", encoding="utf-8")
    operations = [FileOperation(action="write", path="src/app.py", content="new\n")]
    pre = snapshot_preimages(tmp_path, operations)
    path.write_text("new\n", encoding="utf-8")
    post = snapshot_postimages(tmp_path, ["src/app.py"])
    record = MutationRecord(
        operation_batch_id="batch_1",
        phase_id="mutate",
        proposed_operations=operations,
        applied_operations=[{"action": "write", "path": "src/app.py"}],
        preimages=pre,
        postimages=post,
        touched_paths=["src/app.py"],
    )
    mutation_ledger = MutationLedger()
    artifact_ledger = ArtifactLedger()

    mutation_ledger.append(record)
    artifact_ledger.append(record.to_artifact())

    assert "src/app.py" in mutation_ledger.compact_view()["touched_paths"]
    assert "-old" in record.patch_diff()
    assert "+new" in record.patch_diff()
    assert artifact_ledger.by_id("mutation_batch_1") is not None
