from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from appv24.workflows.catalog import built_in_capability_catalog
from appv24.workflows.challenge_corpus import workflow_repair_challenges
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.repair import build_workflow_repair_prompt
from appv24.workflows.validator import validate_workflow_documents
from appv24.workflows.validator import validate_workflow_draft_text
import yaml


def test_built_in_catalog_contains_draft_safe_actions() -> None:
    catalog = built_in_capability_catalog()

    assert catalog.require("ticket.create_draft").execution_mode == "brokered"
    assert catalog.require("telegram.send_message_draft").side_effect_class == "send"
    assert catalog.require("gmail.create_draft").side_effect_class == "create"
    assert catalog.require("github.create_issue_draft").dry_run_supported is True


def test_catalog_rejects_unknown_brokered_action() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    connectors = deepcopy(example.documents.connectors)
    connectors["connectors"][0]["actions"][0]["id"] = "unknown_external_send"
    workflow = deepcopy(example.documents.workflow)
    workflow["steps"][1]["uses"] = "ticket.unknown_external_send"
    documents = replace(example.documents, workflow=workflow, connectors=connectors)

    result = validate_workflow_documents(documents)

    assert "unknown_catalog_action" in {issue.code for issue in result.issues}


def test_catalog_rejects_side_effect_metadata_mismatch() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    connectors = deepcopy(example.documents.connectors)
    connectors["connectors"][0]["actions"][0]["side_effect_class"] = "delete"
    documents = replace(example.documents, connectors=connectors)

    result = validate_workflow_documents(documents)

    assert "catalog_metadata_mismatch" in {issue.code for issue in result.issues}


def _draft_text(challenge_name: str) -> str:
    documents = workflow_repair_challenges()[challenge_name].documents
    fixture_blocks = "\n\n".join(
        f"```yaml dry_run_fixtures/{fixture_name}\n{yaml.safe_dump(fixture, sort_keys=False)}```"
        for fixture_name, fixture in sorted(documents.dry_run_fixtures.items())
    )
    return "\n\n".join(
        block
        for block in (
            f"```yaml workflow.yaml\n{yaml.safe_dump(documents.workflow, sort_keys=False)}```",
            f"```yaml connectors.yaml\n{yaml.safe_dump(documents.connectors, sort_keys=False)}```",
            f"```yaml permissions.yaml\n{yaml.safe_dump(documents.permissions, sort_keys=False)}```",
            fixture_blocks,
            f"```markdown rollback.md\n{documents.rollback_text or ''}```",
        )
        if block.strip()
    )


def test_catalog_repair_prompt_explains_unknown_action_fix() -> None:
    result = validate_workflow_draft_text(_draft_text("unknown_catalog_action"))

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None, max_issues=12)

    assert "unknown_catalog_action" in prompt
    assert "Use a built-in catalog action" in prompt
    assert "declare a strict external connector interface" in prompt
    assert "custom: true and execution_mode: design_only" in prompt


def test_catalog_repair_prompt_explains_metadata_mismatch_fix() -> None:
    result = validate_workflow_draft_text(_draft_text("catalog_metadata_mismatch"))

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None, max_issues=12)

    assert "catalog_metadata_mismatch" in prompt
    assert "must match catalog value" in prompt
    assert "Update only the mismatched catalog metadata fields" in prompt
