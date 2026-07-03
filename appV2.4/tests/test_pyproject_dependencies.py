from __future__ import annotations

import tomllib
import json
from pathlib import Path


def test_base_install_keeps_playwright_optional() -> None:
    pyproject = tomllib.loads(Path("appV2.4/pyproject.toml").read_text())

    dependencies = pyproject["project"]["dependencies"]
    optional_dependencies = pyproject["project"]["optional-dependencies"]

    assert not any(dependency.startswith("playwright") for dependency in dependencies)
    assert "browser" in optional_dependencies
    assert any(
        dependency.startswith("playwright")
        for dependency in optional_dependencies["browser"]
    )


def test_package_metadata_sets_appv24_config_defaults() -> None:
    pyproject = tomllib.loads(Path("appV2.4/pyproject.toml").read_text())
    package_json = json.loads(Path("appV2.4/package.json").read_text())

    assert pyproject["project"]["version"] == "2.4.0"
    assert package_json["version"] == "2.4.0"
    assert package_json["appv24Config"] == {
        "name": "appv24",
        "configDir": ".appv24",
    }
