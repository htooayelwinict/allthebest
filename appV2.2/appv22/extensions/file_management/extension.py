from __future__ import annotations

from appv22.extensions.file_management.mutation_executor import FileMutationExecutor
from appv22.extensions.file_management.mutation_policy import FileMoveMutationPolicy
from appv22.extensions.file_management.planner import FileCleanupPlanner
from appv22.extensions.file_management.schemas import WORKSPACE_MANIFEST_SCHEMA
from appv22.extensions.file_management.skills import FILE_MANAGEMENT_SKILL
from appv22.extensions.file_management.tools import register_file_management_tools
from appv22.extensions.file_management.verifier import WorkspaceManifestVerifier


class FileManagementExtension:
    extension_id = "file_management"

    def skill_cards(self):
        return [FILE_MANAGEMENT_SKILL]

    def register_tools(self, registry) -> None:
        register_file_management_tools(registry)

    def register_capabilities(self, capabilities) -> None:
        capabilities.register_planner("file_management.cleanup_planner", FileCleanupPlanner())
        capabilities.register_mutation_policy("file_management.safe_file_moves", FileMoveMutationPolicy())
        capabilities.register_mutation_executor(
            "file_management.file_mutation_executor", FileMutationExecutor()
        )
        capabilities.register_verifier("file_management.manifest_verifier", WorkspaceManifestVerifier())
        capabilities.register_artifact_schema(
            "file_management.workspace_manifest", WORKSPACE_MANIFEST_SCHEMA
        )
