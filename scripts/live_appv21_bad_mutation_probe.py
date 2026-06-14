"""Probe AppV2.1 denial of an unsafe mutation intent."""

from __future__ import annotations

from appv21_probe_common import seed_repo, write_report

from appv21 import AppV21AgentRuntime
from appv21.runtime.decisions import RuntimeDecision
from appv21.runtime.services import create_appv21_runtime_services


class BadMutationProvider:
    provider_id = "bad-mutation"

    def decide(self, prompt_payload: dict) -> RuntimeDecision:
        return RuntimeDecision(
            kind="mutation_intent",
            reason="Attempt unsafe write.",
            payload={"operation_batch_id": "bad", "operations": [{"action": "write", "path": "../escape.txt", "content": "no"}]},
        )


def main() -> int:
    repo = seed_repo("live_appv21_bad_mutation_repo")
    services = create_appv21_runtime_services(root_path=repo, provider=BadMutationProvider())
    result = AppV21AgentRuntime(root_path=repo, services=services, max_turns=1).run("Try an unsafe write.")
    write_report("live-appv21-bad-mutation-probe.json", result)
    return 0 if result["status"] == "failed" and result.get("reason") == "mutation_denied" else 1


if __name__ == "__main__":
    raise SystemExit(main())
