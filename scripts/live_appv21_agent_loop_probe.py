"""Probe AppV2.1 observe -> plan -> mutate -> verify -> finalize."""

from __future__ import annotations

from appv21_probe_common import seed_repo, write_report

from appv21 import AppV21AgentRuntime


def main() -> int:
    repo = seed_repo("live_appv21_agent_loop_repo")
    result = AppV21AgentRuntime(root_path=repo).run("Clean up this workspace.")
    write_report("live-appv21-agent-loop-probe.json", result)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
