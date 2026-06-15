from __future__ import annotations

from appv22.extensions.base import ObservationContract, SkillCard

FILE_MANAGEMENT_SKILL = SkillCard(
    skill_id="file_management.cleanup",
    extension_id="file_management",
    triggers=(
        "clean",
        "cleanup",
        "organize",
        "mess",
        "tidy",
        "workspace",
        "clutter",
        "sane",
        "record",
    ),
    modes=("START", "THINK", "OBSERVE", "PLAN", "ACT", "VERIFY"),
    summary="Safely organize workspace files and record moves, held paths, and collisions.",
    planner_id="file_management.cleanup_planner",
    mutation_policy_id="file_management.safe_file_moves",
    mutation_executor_id="file_management.file_mutation_executor",
    verifier_id="file_management.manifest_verifier",
    tool_ids=("file_management.repo_snapshot", "file_management.read_file"),
    artifact_schema_ids=("file_management.workspace_manifest",),
    observation_contract=ObservationContract(
        evidence_refs=("world://repo_snapshot/latest",),
        evidence_kinds=("file_management.repo_snapshot",),
        preferred_tool_id="file_management.repo_snapshot",
    ),
)
