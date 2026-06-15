from pathlib import Path


def test_runtime_core_does_not_import_file_management_extension():
    runtime_files = list(Path("appV2.2/appv22/runtime").glob("*.py"))
    for path in runtime_files:
        text = path.read_text(encoding="utf-8")
        assert "extensions.file_management" not in text
