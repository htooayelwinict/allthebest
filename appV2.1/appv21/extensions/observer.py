"""Observer extension for AppV2.1."""

from __future__ import annotations

from appv21.state.events import RuntimeEvent
from appv21.tools.broker import ToolBroker


class ObserverExtension:
    def observe_repo(self, broker: ToolBroker) -> list[RuntimeEvent]:
        snapshot = broker.repo_snapshot()
        ref_id = "world://repo_snapshot/latest"
        return [
            RuntimeEvent(
                "WorldRefAdded",
                {
                    "ref_id": ref_id,
                    "kind": "repo_snapshot",
                    "summary": f"{snapshot['prompt_summary']['file_count']} files, {snapshot['prompt_summary']['directory_count']} directories",
                    "payload": snapshot,
                    "trust": "runtime_observed",
                },
            )
        ]
