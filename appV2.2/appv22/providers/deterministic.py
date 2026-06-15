from __future__ import annotations

from appv22.runtime.decisions import RuntimeDecision


class DeterministicAppV22Provider:
    provider_id = "deterministic-appv22"

    def decide(self, prompt: dict) -> RuntimeDecision:
        if not prompt["world"]["world_refs"]:
            return RuntimeDecision(
                "tool_call",
                "observe first",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            )
        if not prompt["state"]["runtime_plan"]:
            return RuntimeDecision("plan", "plan from observed snapshot", evidence_refs=["world://repo_snapshot/latest"])
        if not prompt["state"]["mutation_receipts"]:
            return RuntimeDecision(
                "mutation_intent",
                "apply extension plan",
                prompt["state"]["runtime_plan"]["mutation_intent"],
                ["plan://accepted/latest"],
            )
        return RuntimeDecision("finalize", "verify and finish")
