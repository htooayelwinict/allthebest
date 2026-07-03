from __future__ import annotations


def test_workflow_validator_dependencies_are_importable() -> None:
    import yaml
    from jsonschema import Draft202012Validator

    assert yaml.safe_load("enabled: true\n") == {"enabled": True}
    assert Draft202012Validator.META_SCHEMA["$id"].endswith("/draft/2020-12/schema")
