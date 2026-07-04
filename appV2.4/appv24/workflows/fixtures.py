"""Dry-run fixture helpers for appv24 workflow packages."""

from __future__ import annotations

from typing import Any, Mapping

from appv24.workflows.errors import WorkflowPackageError


def fixture_names(fixtures: Mapping[str, Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(sorted(str(name) for name in fixtures))


def require_fixture(fixtures: Mapping[str, Mapping[str, Any]], name: str) -> Mapping[str, Any]:
    fixture = fixtures.get(name)
    if fixture is not None:
        return fixture
    available = ", ".join(fixture_names(fixtures)) or "none"
    raise WorkflowPackageError(f"missing dry-run fixture: {name}; available: {available}")
