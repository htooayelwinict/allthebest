"""Local no-network artifact providers for workflow broker dry-runs."""

from __future__ import annotations

import json
from pathlib import Path

from appv24.workflows.broker import BrokerRequest, BrokerResult


class LocalArtifactProviders:
    def __init__(self, artifact_root: Path):
        self.artifact_root = artifact_root
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def ticket_create_draft(self, request: BrokerRequest) -> BrokerResult:
        ticket_id = f"local-ticket-{request.step_id}"
        path = self.artifact_root / f"{ticket_id}.json"
        path.write_text(
            json.dumps({"step_id": request.step_id, "input": request.input_ref}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return _filtered(
            request,
            {
                "ticket_id": ticket_id,
                "artifact_path": str(path),
            },
        )

    def gmail_create_draft(self, request: BrokerRequest) -> BrokerResult:
        draft_id = f"local-draft-{request.step_id}"
        path = self.artifact_root / f"{draft_id}.json"
        path.write_text(
            json.dumps({"step_id": request.step_id, "input": request.input_ref}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return _filtered(
            request,
            {
                "draft_id": draft_id,
                "artifact_path": str(path),
            },
        )


def _filtered(request: BrokerRequest, outputs: dict[str, object]) -> BrokerResult:
    allowed = set(request.allowed_outputs)
    return BrokerResult(
        status="simulated",
        provider_id="local_artifacts",
        outputs={key: value for key, value in outputs.items() if key in allowed},
    )
