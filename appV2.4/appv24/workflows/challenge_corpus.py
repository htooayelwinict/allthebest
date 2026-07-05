"""Broken workflow package cases used to regression-test repair behavior."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace

from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.loader import PackageDocuments


@dataclass(frozen=True)
class WorkflowRepairChallenge:
    name: str
    documents: PackageDocuments
    expected_issue_codes: tuple[str, ...]
    expected_repaired_paths: tuple[str, ...]


ChallengeBuilder = Callable[[], WorkflowRepairChallenge]


def workflow_repair_challenges() -> dict[str, WorkflowRepairChallenge]:
    return {name: builder() for name, builder in _BUILDERS.items()}


def _base() -> PackageDocuments:
    return golden_workflow_examples()["telegram_support_ticket"].documents


def _missing_next() -> WorkflowRepairChallenge:
    base = _base()
    workflow = deepcopy(base.workflow)
    workflow["steps"][1].pop("next", None)
    return WorkflowRepairChallenge(
        name="missing_next",
        documents=replace(base, workflow=workflow),
        expected_issue_codes=("missing_transition",),
        expected_repaired_paths=("workflow.yaml:steps.create_ticket_draft.next",),
    )


def _missing_branches() -> WorkflowRepairChallenge:
    base = _base()
    workflow = deepcopy(base.workflow)
    workflow["steps"][0].pop("branches", None)
    return WorkflowRepairChallenge(
        name="missing_branches",
        documents=replace(base, workflow=workflow),
        expected_issue_codes=("missing_branch_transition",),
        expected_repaired_paths=("workflow.yaml:steps.check_authorized_customer.branches",),
    )


def _missing_fixture_decision() -> WorkflowRepairChallenge:
    base = _base()
    fixtures = deepcopy(base.dry_run_fixtures)
    fixtures["happy_path.yaml"].pop("decision", None)
    return WorkflowRepairChallenge(
        name="missing_fixture_decision",
        documents=replace(base, dry_run_fixtures=fixtures),
        expected_issue_codes=("fixture_branch_unmatched",),
        expected_repaired_paths=("dry_run_fixtures/happy_path.yaml:decision.check_authorized_customer",),
    )


def _wrong_fixture_decision_key() -> WorkflowRepairChallenge:
    base = _base()
    fixtures = deepcopy(base.dry_run_fixtures)
    fixtures["happy_path.yaml"] = {"decision": {"check_path": "authorized"}}
    fixtures["rejection_path.yaml"] = {"decision": {"check_path": "rejected"}}
    return WorkflowRepairChallenge(
        name="wrong_fixture_decision_key",
        documents=replace(base, dry_run_fixtures=fixtures),
        expected_issue_codes=("fixture_branch_unmatched",),
        expected_repaired_paths=("dry_run_fixtures/happy_path.yaml:decision.check_authorized_customer",),
    )


def _target_step_fixture_decision() -> WorkflowRepairChallenge:
    base = _base()
    fixtures = deepcopy(base.dry_run_fixtures)
    fixtures["happy_path.yaml"] = {"decision": {"check_authorized_customer": "create_ticket_draft"}}
    fixtures["rejection_path.yaml"] = {"decision": {"check_authorized_customer": "rejected"}}
    return WorkflowRepairChallenge(
        name="target_step_fixture_decision",
        documents=replace(base, dry_run_fixtures=fixtures),
        expected_issue_codes=("fixture_branch_unmatched",),
        expected_repaired_paths=("dry_run_fixtures/happy_path.yaml:decision.check_authorized_customer",),
    )


def _unreachable_terminal() -> WorkflowRepairChallenge:
    base = _base()
    workflow = deepcopy(base.workflow)
    workflow["steps"][1]["next"] = "notify_admin"
    workflow["steps"][2]["next"] = "create_ticket_draft"
    return WorkflowRepairChallenge(
        name="unreachable_terminal",
        documents=replace(base, workflow=workflow),
        expected_issue_codes=("cycle_without_terminal_escape",),
        expected_repaired_paths=("workflow.yaml:steps.notify_admin.next",),
    )


def _bad_approval_metadata() -> WorkflowRepairChallenge:
    base = _base()
    permissions = deepcopy(base.permissions)
    permissions["approvals"][0]["required_for"] = ["create_ticket_draft"]
    return WorkflowRepairChallenge(
        name="bad_approval_metadata",
        documents=replace(base, permissions=permissions),
        expected_issue_codes=("missing_approval",),
        expected_repaired_paths=("permissions.yaml:approvals.0.required_for",),
    )


def _design_only_action_used() -> WorkflowRepairChallenge:
    base = _base()
    connectors = deepcopy(base.connectors)
    connectors["connectors"][0]["actions"][0]["execution_mode"] = "design_only"
    return WorkflowRepairChallenge(
        name="design_only_action_used",
        documents=replace(base, connectors=connectors),
        expected_issue_codes=("design_only_action_used", "invalid_side_effect_contract"),
        expected_repaired_paths=("workflow.yaml:steps.create_ticket_draft",),
    )


def _unknown_catalog_action() -> WorkflowRepairChallenge:
    base = _base()
    workflow = deepcopy(base.workflow)
    connectors = deepcopy(base.connectors)
    connectors["connectors"][0]["actions"][0]["id"] = "unknown_external_send"
    workflow["steps"][1]["uses"] = "ticket.unknown_external_send"
    return WorkflowRepairChallenge(
        name="unknown_catalog_action",
        documents=replace(base, workflow=workflow, connectors=connectors),
        expected_issue_codes=("unknown_catalog_action",),
        expected_repaired_paths=("connectors.yaml:connectors.0.actions.0.id",),
    )


def _catalog_metadata_mismatch() -> WorkflowRepairChallenge:
    base = _base()
    connectors = deepcopy(base.connectors)
    connectors["connectors"][0]["actions"][0]["side_effect_class"] = "delete"
    return WorkflowRepairChallenge(
        name="catalog_metadata_mismatch",
        documents=replace(base, connectors=connectors),
        expected_issue_codes=("catalog_metadata_mismatch",),
        expected_repaired_paths=("connectors.yaml:connectors.0.actions.0.side_effect_class",),
    )


_BUILDERS: dict[str, ChallengeBuilder] = {
    "missing_next": _missing_next,
    "missing_branches": _missing_branches,
    "missing_fixture_decision": _missing_fixture_decision,
    "wrong_fixture_decision_key": _wrong_fixture_decision_key,
    "target_step_fixture_decision": _target_step_fixture_decision,
    "unreachable_terminal": _unreachable_terminal,
    "bad_approval_metadata": _bad_approval_metadata,
    "design_only_action_used": _design_only_action_used,
    "unknown_catalog_action": _unknown_catalog_action,
    "catalog_metadata_mismatch": _catalog_metadata_mismatch,
}
