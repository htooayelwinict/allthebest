import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.2"))

from appv22.extensions.base import RuntimeExtension, SkillCard
from appv22.extensions.registry import ExtensionRegistry
from appv22.runtime.capabilities import CapabilityRegistry
from appv22.state.models import AgentState, RequestEnvelope


class DemoPlanner:
    capability_id = "demo.planner"


class DemoVerifier:
    capability_id = "demo.verifier"


class DemoMutationPolicy:
    capability_id = "demo.policy"


class DemoMutationExecutor:
    capability_id = "demo.executor"


class DemoExtension(RuntimeExtension):
    extension_id = "demo"

    def skill_cards(self):
        return [
            SkillCard(
                "demo.cleanup",
                "demo",
                ("clean",),
                ("START", "PLAN", "ACT"),
                "Demo",
                "demo.planner",
                "demo.policy",
                "demo.executor",
                "demo.verifier",
                ("demo.inspect",),
                ("demo.schema",),
            )
        ]

    def register_capabilities(self, capabilities: CapabilityRegistry):
        capabilities.register_planner("demo.planner", DemoPlanner())
        capabilities.register_mutation_policy("demo.policy", DemoMutationPolicy())
        capabilities.register_mutation_executor("demo.executor", DemoMutationExecutor())
        capabilities.register_verifier("demo.verifier", DemoVerifier())
        capabilities.register_artifact_schema("demo.schema", {"type": "object"})


def test_extension_resolution_links_skill_to_capabilities():
    registry = ExtensionRegistry()
    capabilities = CapabilityRegistry()
    extension = DemoExtension()
    registry.register(extension)
    extension.register_capabilities(capabilities)
    state = AgentState("sess", "run", RequestEnvelope("req", "please clean", "."))

    resolved = registry.resolve_active(state)

    assert resolved.extension_ids == ["demo"]
    assert resolved.planner_ids == ["demo.planner"]
    assert resolved.mutation_policy_ids == ["demo.policy"]
    assert resolved.mutation_executor_ids == ["demo.executor"]
    assert resolved.verifier_ids == ["demo.verifier"]
    assert resolved.artifact_schema_ids == ["demo.schema"]
    assert capabilities.planner("demo.planner").capability_id == "demo.planner"
    assert capabilities.mutation_policy("demo.policy").capability_id == "demo.policy"
    assert capabilities.mutation_executor("demo.executor").capability_id == "demo.executor"
    assert capabilities.verifier("demo.verifier").capability_id == "demo.verifier"
    assert capabilities.artifact_schema("demo.schema") == {"type": "object"}


def test_extension_resolution_ignores_inactive_skills():
    registry = ExtensionRegistry()
    registry.register(DemoExtension())
    state = AgentState("sess", "run", RequestEnvelope("req", "leave it alone", "."))

    resolved = registry.resolve_active(state)

    assert resolved.extension_ids == []
    assert resolved.skill_cards == []
    assert resolved.planner_ids == []
