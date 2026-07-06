from __future__ import annotations

import yaml

from appv24.workflows.challenge_corpus import workflow_repair_challenges
from appv24.workflows.quality_gate import check_workflow_quality_documents
from appv24.workflows.repair import build_workflow_repair_prompt
from appv24.workflows.validator import validate_workflow_draft_text


def test_workflow_repair_challenge_inventory_has_required_cases() -> None:
    names = set(workflow_repair_challenges())

    assert {
        "missing_next",
        "missing_branches",
        "missing_fixture_decision",
        "wrong_fixture_decision_key",
        "target_step_fixture_decision",
        "unreachable_terminal",
        "bad_approval_metadata",
        "design_only_action_used",
        "unknown_catalog_action",
        "catalog_metadata_mismatch",
    } <= names


def test_workflow_repair_challenges_fail_with_expected_issue_codes() -> None:
    for name, challenge in workflow_repair_challenges().items():
        result = check_workflow_quality_documents(challenge.documents)
        codes = {issue.code for issue in result.issues}

        assert not result.ok, name
        assert set(challenge.expected_issue_codes) & codes, (name, codes)


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


def test_workflow_repair_challenge_prompts_include_actionable_directives() -> None:
    for name, challenge in workflow_repair_challenges().items():
        result = validate_workflow_draft_text(_draft_text(name))
        prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None, max_issues=12)

        for code in challenge.expected_issue_codes:
            assert code in prompt, (name, code)
        for repaired_path in challenge.expected_repaired_paths:
            assert repaired_path.split(":", 1)[0] in prompt, (name, repaired_path)
