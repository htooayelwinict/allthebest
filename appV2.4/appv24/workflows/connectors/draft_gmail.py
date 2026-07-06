"""Draft-only Gmail provider for appv24 workflow broker tests."""

from __future__ import annotations

from appv24.workflows.broker import BrokerRequest, BrokerResult


def gmail_create_draft_provider(request: BrokerRequest) -> BrokerResult:
    if not request.dry_run:
        return BrokerResult(
            status="blocked",
            provider_id="gmail_draft",
            outputs={},
            issues=("gmail draft provider only supports dry-run in appv24 phase 5",),
        )
    return BrokerResult(
        status="simulated",
        provider_id="gmail_draft",
        outputs={
            "draft_id": f"dryrun-{request.step_id}",
            "thread_id": f"thread-{request.step_id}",
        },
    )
