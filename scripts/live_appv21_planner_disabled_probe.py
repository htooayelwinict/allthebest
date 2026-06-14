"""Probe AppV2.1 with planner extension unused."""

from __future__ import annotations

from appv21_probe_common import seed_repo, write_report

from appv21 import AppV21AgentRuntime
from appv21.runtime.decisions import RuntimeDecision
from appv21.runtime.services import create_appv21_runtime_services


class PlannerDisabledProvider:
    provider_id = "planner-disabled"

    def decide(self, prompt_payload: dict) -> RuntimeDecision:
        if not prompt_payload["world"]["world_refs"]:
            return RuntimeDecision(kind="tool_call", reason="Read a file without planning.", payload={"tool_name": "read_file", "arguments": {"path": "README.md"}})
        return RuntimeDecision(kind="finalize", reason="Safe no-op complete.", payload={"explicit_noop": True})


def main() -> int:
    repo = seed_repo("live_appv21_planner_disabled_repo")
    services = create_appv21_runtime_services(root_path=repo, provider=PlannerDisabledProvider())
    result = AppV21AgentRuntime(root_path=repo, services=services).run("Inspect without planning.")
    write_report("live-appv21-planner-disabled-probe.json", result)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
