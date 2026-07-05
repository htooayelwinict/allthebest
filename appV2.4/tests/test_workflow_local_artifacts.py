from __future__ import annotations

from pathlib import Path

from appv24.workflows.broker import BrokerRequest
from appv24.workflows.local_artifacts import LocalArtifactProviders


def test_ticket_draft_provider_writes_only_under_artifact_root(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)
    result = providers.ticket_create_draft(
        BrokerRequest(
            step_id="create_ticket_draft",
            connector_action="ticket.create_draft",
            input_ref={"with": {"title": "Support request"}},
            fixture={},
            allowed_outputs=("ticket_id", "artifact_path"),
        )
    )

    assert result.status == "simulated"
    assert result.outputs["ticket_id"].startswith("local-ticket-")
    artifact_path = Path(str(result.outputs["artifact_path"]))
    assert artifact_path.is_file()
    assert artifact_path.is_relative_to(tmp_path)


def test_local_artifact_provider_filters_unallowed_outputs(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)
    result = providers.gmail_create_draft(
        BrokerRequest(
            step_id="create_gmail_draft",
            connector_action="gmail.create_draft",
            input_ref={},
            fixture={},
            allowed_outputs=("draft_id",),
        )
    )

    assert result.outputs == {"draft_id": "local-draft-create_gmail_draft"}
