from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "appV2.2"))

from appv22 import AppV22AgentRuntime
from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.providers.deterministic import DeterministicAppV22Provider
from appv22.runtime.services import create_appv22_services


def test_agent_loop_uses_capability_registry_without_file_imports(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text("a", encoding="utf-8")
    services = create_appv22_services(
        root_path=tmp_path,
        provider=DeterministicAppV22Provider(),
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=8).run(
        "make this workspace sane and keep a record"
    )

    assert result["status"] == "completed"
    assert (tmp_path / "docs" / "a.md").is_file()
    assert result["mutation_receipts"]
    assert result["verification_receipts"]


def test_agent_loop_fails_when_max_turns_exceeded(tmp_path):
    services = create_appv22_services(
        root_path=tmp_path,
        provider=DeterministicAppV22Provider(),
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=0).run(
        "make this workspace sane and keep a record"
    )

    assert result["status"] == "failed"
    assert result["reason"] == "max_turns_exceeded"
