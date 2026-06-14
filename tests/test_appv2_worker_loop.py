import json
from pathlib import Path
from typing import Any

from appV2.schemas import ArtifactRecord, Envelope, PhasePlan
from appV2.worker.runtime import WorkerRuntime
from tests.test_appv2_phase_planner import _envelope, _plan


class QueueClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        self.prompts.append(prompt)
        return json.dumps(self.responses.pop(0))


class ReplanRecorder:
    def __init__(self, replacement_plan: PhasePlan) -> None:
        self.replacement_plan = replacement_plan
        self.requests: list[Any] = []

    def replan(self, envelope, current_plan, replan_request, *, trace=None):
        self.requests.append(replan_request)
        return self.replacement_plan


def test_worker_feedback_loop_repairs_denied_tool_call(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "discover",
                    "phase": "DISCOVER",
                    "goal": "Inspect repository.",
                    "instructions": ["Read repo state."],
                    "input_artifacts": [],
                    "output_artifacts": ["repo_inventory"],
                    "allowed_tool_groups": ["repo_read"],
                    "max_tool_calls": 1,
                    "max_model_calls": 3,
                }
            ],
        }
    )
    client = QueueClient(
        [
            {"tool_calls": [{"call_id": "bad", "tool_name": "write_file", "arguments": {"path": "x"}, "purpose": "bad"}]},
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "repo inspected after feedback",
                    "artifacts": [
                        {
                            "id": "repo_inventory",
                            "kind": "phase_output",
                            "content": {"files": []},
                            "producer": "worker",
                        }
                    ],
                }
            },
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path).run(plan, envelope=_envelope())

    assert result.status == "completed"
    assert "tool_group_not_allowed" in client.prompts[1]


def test_worker_normalizes_top_level_tool_call_shape(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "discover",
                    "phase": "DISCOVER",
                    "goal": "Inspect repository.",
                    "instructions": ["Read repo state."],
                    "input_artifacts": [],
                    "output_artifacts": ["repo_inventory"],
                    "allowed_tool_groups": ["repo_read"],
                    "max_tool_calls": 1,
                    "max_model_calls": 2,
                }
            ],
            "artifact_contracts": [{"id": "repo_inventory"}],
        }
    )
    client = QueueClient(
        [
            {"call_id": "scan", "tool_name": "repo_snapshot", "arguments": {"path": "."}, "purpose": "scan root"},
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "repo inspected",
                    "artifacts": [
                        {
                            "id": "repo_inventory",
                            "kind": "phase_output",
                            "content": {"files": []},
                            "producer": "worker",
                        }
                    ],
                }
            },
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path).run(plan, envelope=_envelope())

    assert result.status == "completed"


def test_worker_runtime_treats_request_envelope_as_runtime_scope_input(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "finalize",
                    "phase": "FINALIZE",
                    "goal": "Summarize the scoped request.",
                    "instructions": ["Use runtime scope and produce the final report."],
                    "input_artifacts": ["request_envelope"],
                    "output_artifacts": ["final_report"],
                    "allowed_tool_groups": [],
                    "max_tool_calls": 0,
                    "max_model_calls": 1,
                }
            ],
            "artifact_contracts": [{"id": "final_report"}],
        }
    )
    client = QueueClient(
        [
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "scoped by envelope",
                    "artifacts": [
                        {
                            "id": "final_report",
                            "kind": "final_report",
                            "content": {"summary": "used runtime scope"},
                            "producer": "worker",
                        }
                    ],
                }
            }
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path).run(plan, envelope=_envelope())

    assert result.status == "completed"


def test_worker_runtime_treats_declared_scope_input_contract_as_available(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "finalize",
                    "phase": "FINALIZE",
                    "goal": "Summarize the scoped request.",
                    "instructions": ["Use declared runtime scope and produce the final report."],
                    "input_artifacts": ["repo_scope_input"],
                    "output_artifacts": ["final_report"],
                    "allowed_tool_groups": [],
                    "max_tool_calls": 0,
                    "max_model_calls": 1,
                }
            ],
            "artifact_contracts": [{"id": "repo_scope_input", "kind": "input"}, {"id": "final_report"}],
        }
    )
    client = QueueClient(
        [
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "scoped by declared runtime input",
                    "artifacts": [
                        {
                            "id": "final_report",
                            "kind": "final_report",
                            "content": {"summary": "used declared runtime scope"},
                            "producer": "worker",
                        }
                    ],
                }
            }
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path).run(plan, envelope=_envelope())

    assert result.status == "completed"


def test_worker_mutation_denial_feedback_then_repair(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "mutate",
                    "phase": "MUTATE",
                    "goal": "Write allowed file.",
                    "instructions": ["Write only approved path."],
                    "input_artifacts": [],
                    "output_artifacts": ["change_summary"],
                        "allowed_tool_groups": ["file_write"],
                        "mutation_policy": {"mode": "strict", "allowed_paths": ["docs/allowed.md"], "max_files": 2},
                        "max_tool_calls": 0,
                        "max_model_calls": 3,
                    },
                {
                    "phase_id": "verify",
                    "phase": "VERIFY",
                    "goal": "Verify mutation evidence.",
                    "instructions": ["Use runtime evidence."],
                    "input_artifacts": ["change_summary"],
                    "output_artifacts": ["verification_results"],
                    "allowed_tool_groups": ["verify"],
                    "verification_policy": {"required": True, "require_evidence": True},
                    "max_tool_calls": 0,
                    "max_model_calls": 2,
                },
            ],
        }
    )
    client = QueueClient(
        [
            {
                "mutation": {
                    "operation_batch_id": "bad",
                    "operations": [{"action": "write", "path": "docs/nope.md", "content": "bad"}],
                    "reason": "try bad path",
                }
            },
            {
                "mutation": {
                    "operation_batch_id": "good",
                    "operations": [{"action": "write", "path": "docs/allowed.md", "content": "ok"}],
                    "reason": "repair path",
                }
            },
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "changed allowed file",
                    "artifacts": [
                        {
                            "id": "change_summary",
                            "kind": "phase_output",
                            "content": {"changed_paths": ["docs/allowed.md"]},
                            "producer": "worker",
                        }
                    ],
                }
            },
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "verification passed with mutation evidence",
                    "artifacts": [
                        {
                            "id": "verification_results",
                            "kind": "verification_evidence",
                            "content": {"status": "passed"},
                            "producer": "worker",
                            "trust_level": "runtime_verified",
                        }
                    ],
                }
            },
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path).run(plan, envelope=_envelope())

    assert result.status == "completed"
    assert (tmp_path / "docs/allowed.md").read_text(encoding="utf-8") == "ok"
    assert "path_not_in_strict_policy" in client.prompts[1]


def test_worker_artifact_validation_feedback_then_repair(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "finalize",
                    "phase": "FINALIZE",
                    "goal": "Summarize.",
                    "instructions": ["Produce final report."],
                    "input_artifacts": [],
                    "output_artifacts": ["final_report"],
                    "allowed_tool_groups": [],
                    "max_tool_calls": 0,
                    "max_model_calls": 3,
                }
            ],
        }
    )
    client = QueueClient(
        [
            {"final_phase_output": {"status": "completed", "summary": "missing artifact", "artifacts": []}},
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "final report ready",
                    "artifacts": [
                        {
                            "id": "final_report",
                            "kind": "final_report",
                            "content": {"summary": "done"},
                            "producer": "worker",
                        }
                    ],
                }
            },
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path).run(plan, envelope=_envelope())

    assert result.status == "completed"
    assert "phase_output_validation_failed" in client.prompts[1]


def test_worker_repairs_invalid_artifact_record_shape_after_feedback(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "discover",
                    "phase": "DISCOVER",
                    "goal": "Inspect repository.",
                    "instructions": ["Produce discovery observations."],
                    "input_artifacts": [],
                    "output_artifacts": ["repo_inventory"],
                    "allowed_tool_groups": [],
                    "max_tool_calls": 0,
                    "max_model_calls": 3,
                }
            ],
            "artifact_contracts": [
                {
                    "id": "repo_inventory",
                    "kind": "phase_output",
                    "content_schema": {"type": "object"},
                }
            ],
        }
    )
    client = QueueClient(
        [
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "bad artifact shape",
                    "artifacts": [
                        {
                            "id": "repo_inventory",
                            "kind": "phase_output",
                            "content": {"files": ["README.md"]},
                            "producer": "worker",
                            "summary": "extra top-level summary",
                            "lifecycle": "output",
                        }
                    ],
                }
            },
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "good artifact shape",
                    "artifacts": [
                        {
                            "id": "repo_inventory",
                            "kind": "phase_output",
                            "content": {"files": ["README.md"], "summary": "repo inventory captured"},
                            "producer": "worker",
                            "lifecycle": "completed",
                        }
                    ],
                }
            },
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path).run(plan, envelope=_envelope())

    assert result.status == "completed"
    assert "artifact top level" in client.prompts[1] or "ArtifactRecord" in client.prompts[1]


def test_worker_repair_feedback_is_shape_aware_for_top_level_tool_fields(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "discover",
                    "phase": "DISCOVER",
                    "goal": "Inspect repository.",
                    "instructions": ["Read repo state."],
                    "input_artifacts": [],
                    "output_artifacts": ["repo_inventory"],
                    "allowed_tool_groups": ["repo_read"],
                    "max_tool_calls": 1,
                    "max_model_calls": 3,
                }
            ],
            "artifact_contracts": [{"id": "repo_inventory"}],
        }
    )
    client = QueueClient(
        [
            {"tool_name": "read_file", "arguments": {"path": "README.md"}, "purpose": "inspect file"},
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "repo inspected",
                    "artifacts": [
                        {
                            "id": "repo_inventory",
                            "kind": "phase_output",
                            "content": {"files": []},
                            "producer": "worker",
                        }
                    ],
                }
            },
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path).run(plan, envelope=_envelope())

    assert result.status == "completed"
    assert "Wrap them inside tool_calls" in client.prompts[1]


def test_worker_normalizes_stringified_final_phase_output_branch(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "finalize",
                    "phase": "FINALIZE",
                    "goal": "Summarize.",
                    "instructions": ["Produce final report."],
                    "input_artifacts": [],
                    "output_artifacts": ["final_report"],
                    "allowed_tool_groups": [],
                    "max_tool_calls": 0,
                    "max_model_calls": 1,
                }
            ],
            "artifact_contracts": [{"id": "final_report"}],
        }
    )
    client = QueueClient(
        [
            {
                "final_phase_output": json.dumps(
                    {
                        "status": "completed",
                        "summary": "stringified nested branch",
                        "artifacts": [
                            {
                                "id": "final_report",
                                "kind": "final_report",
                                "content": {"summary": "done"},
                                "producer": "worker",
                            }
                        ],
                    }
                )
            }
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path).run(plan, envelope=_envelope())

    assert result.status == "completed"


def test_worker_normalizes_marshaled_final_phase_output_branch(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "finalize",
                    "phase": "FINALIZE",
                    "goal": "Summarize.",
                    "instructions": ["Produce final report."],
                    "input_artifacts": [],
                    "output_artifacts": ["final_report"],
                    "allowed_tool_groups": [],
                    "max_tool_calls": 0,
                    "max_model_calls": 1,
                }
            ],
            "artifact_contracts": [{"id": "final_report"}],
        }
    )
    client = QueueClient(
        [
            {
                "final_phase_output": {
                    "type": "Object",
                    "completionState": "complete",
                    "entries": [
                        ["summary", "marshaled nested branch"],
                        [
                            "artifacts",
                            {
                                "type": "Array",
                                "entries": [
                                    [
                                        0,
                                        {
                                            "type": "Object",
                                            "entries": [
                                                ["id", "final_report"],
                                                ["kind", "final_report"],
                                                ["content", {"type": "Object", "entries": [["summary", "done"]]}],
                                                ["producer", "worker"],
                                            ],
                                        },
                                    ]
                                ],
                            },
                        ],
                    ],
                }
            }
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path).run(plan, envelope=_envelope())

    assert result.status == "completed"


def test_worker_normalizes_stringified_marshaled_final_phase_output_branch(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "finalize",
                    "phase": "FINALIZE",
                    "goal": "Summarize.",
                    "instructions": ["Produce final report."],
                    "input_artifacts": [],
                    "output_artifacts": ["final_report"],
                    "allowed_tool_groups": [],
                    "max_tool_calls": 0,
                    "max_model_calls": 1,
                }
            ],
            "artifact_contracts": [{"id": "final_report"}],
        }
    )
    client = QueueClient(
        [
            {
                "final_phase_output": json.dumps(
                    {
                        "type": "Object",
                        "completionState": "complete",
                        "entries": [
                            ["summary", "stringified marshaled nested branch"],
                            [
                                "artifacts",
                                {
                                    "type": "Array",
                                    "entries": [
                                        [
                                            0,
                                            {
                                                "type": "Object",
                                                "entries": [
                                                    ["id", "final_report"],
                                                    ["kind", "final_report"],
                                                    ["content", {"type": "Object", "entries": [["summary", "done"]]}],
                                                    ["producer", "worker"],
                                                ],
                                            },
                                        ]
                                    ],
                                },
                            ],
                        ],
                    }
                )
            }
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path).run(plan, envelope=_envelope())

    assert result.status == "completed"


def test_worker_tool_phase_budget_floor_allows_repair_turn(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "discover",
                    "phase": "DISCOVER",
                    "goal": "Inspect repository.",
                    "instructions": ["Use one tool call, then finalize."],
                    "input_artifacts": [],
                    "output_artifacts": ["repo_inventory"],
                    "allowed_tool_groups": ["repo_read"],
                    "max_tool_calls": 1,
                    "max_model_calls": 2,
                }
            ],
            "artifact_contracts": [{"id": "repo_inventory"}],
        }
    )
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "call_id": "repo_snapshot",
                        "tool_name": "repo_snapshot",
                        "arguments": {},
                        "purpose": "capture repository state",
                    }
                ]
            },
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "bad artifact shape",
                    "artifacts": [
                        {
                            "id": "repo_inventory",
                            "kind": "phase_output",
                            "content": {"files": []},
                            "producer": "worker",
                            "summary": "extra top-level summary",
                            "lifecycle": "output",
                        }
                    ],
                }
            },
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "good artifact shape",
                    "artifacts": [
                        {
                            "id": "repo_inventory",
                            "kind": "phase_output",
                            "content": {"files": []},
                            "producer": "worker",
                            "lifecycle": "completed",
                        }
                    ],
                }
            },
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path).run(plan, envelope=_envelope())

    assert result.status == "completed"


def test_worker_budget_ceiling_stops_feedback_loop(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "finalize",
                    "phase": "FINALIZE",
                    "goal": "Summarize.",
                    "instructions": ["Produce final report."],
                    "input_artifacts": [],
                    "output_artifacts": ["final_report"],
                    "allowed_tool_groups": [],
                    "max_tool_calls": 0,
                    "max_model_calls": 1,
                }
            ],
        }
    )
    client = QueueClient(
        [
            {"final_phase_output": {"status": "completed", "summary": "missing", "artifacts": []}},
            {"final_phase_output": {"status": "completed", "summary": "still missing", "artifacts": []}},
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path).run(plan, envelope=_envelope())

    assert result.status == "budget_exceeded"
    assert any(issue.code == "model_budget_exceeded" for issue in result.issues)


def test_worker_runtime_internal_replan_preserves_completed_artifacts(tmp_path: Path) -> None:
    initial_plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "discover",
                    "phase": "DISCOVER",
                    "goal": "Inspect repository.",
                    "instructions": ["Produce inventory."],
                    "input_artifacts": [],
                    "output_artifacts": ["repo_inventory"],
                    "allowed_tool_groups": [],
                    "max_tool_calls": 0,
                    "max_model_calls": 1,
                },
                {
                    "phase_id": "analyze",
                    "phase": "ANALYZE",
                    "goal": "Analyze discovered planner drift.",
                    "instructions": ["Worker discovers the next planned work no longer matches repo state."],
                    "input_artifacts": ["repo_inventory"],
                    "output_artifacts": ["analysis_report"],
                    "allowed_tool_groups": [],
                    "max_tool_calls": 0,
                    "max_model_calls": 1,
                },
            ],
            "artifact_contracts": [{"id": "repo_inventory"}, {"id": "analysis_report"}],
        }
    )
    replacement_plan = PhasePlan.model_validate(
        {
            **_plan(),
            "plan_id": "v2_plan_replanned",
            "phases": [
                {
                    "phase_id": "finalize_replanned",
                    "phase": "FINALIZE",
                    "goal": "Use carryover inventory to finish.",
                    "instructions": ["Finish with preserved evidence."],
                    "input_artifacts": ["repo_inventory"],
                    "output_artifacts": ["final_report"],
                    "allowed_tool_groups": [],
                    "max_tool_calls": 0,
                    "max_model_calls": 1,
                }
            ],
            "artifact_contracts": [{"id": "repo_inventory"}, {"id": "final_report"}],
        }
    )
    planner = ReplanRecorder(replacement_plan)
    client = QueueClient(
        [
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "inventory ready",
                    "artifacts": [
                        {
                            "id": "repo_inventory",
                            "kind": "phase_output",
                            "content": {"files": ["README.md"]},
                            "producer": "worker",
                        }
                    ],
                }
            },
            {
                "planner_replan_signal": {
                    "reason": "repo inventory contradicts planner assumptions",
                    "phase_id": "analyze",
                    "issue_codes": ["repo_plan_drift"],
                    "recommended_action": "replace analyze and downstream phases",
                }
            },
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "finished after replan",
                    "artifacts": [
                        {
                            "id": "final_report",
                            "kind": "final_report",
                            "content": {"summary": "used preserved inventory"},
                            "producer": "worker",
                        }
                    ],
                }
            },
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path, planner_runtime=planner).run(initial_plan, envelope=_envelope())

    assert result.status == "completed"
    assert result.plan_id == "v2_plan_replanned"
    assert len(planner.requests) == 1
    assert planner.requests[0].failed_phase_id == "analyze"
    assert [artifact.id for artifact in planner.requests[0].completed_artifacts] == ["repo_inventory"]
    assert result.usage["replans"] == 1


def test_worker_runtime_rejects_runtime_owned_replan_signal(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "finalize",
                    "phase": "FINALIZE",
                    "goal": "Finalize.",
                    "instructions": ["Do not replan for runtime errors."],
                    "input_artifacts": [],
                    "output_artifacts": ["final_report"],
                    "allowed_tool_groups": [],
                    "max_tool_calls": 0,
                    "max_model_calls": 1,
                }
            ],
            "artifact_contracts": [{"id": "final_report"}],
        }
    )
    planner = ReplanRecorder(plan)
    client = QueueClient(
        [
            {
                "planner_replan_signal": {
                    "reason": "tool failed",
                    "phase_id": "finalize",
                    "issue_codes": ["tool_execution_failed"],
                    "recommended_action": "try a different tool",
                }
            }
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path, planner_runtime=planner).run(plan, envelope=_envelope())

    assert result.status == "blocked"
    assert planner.requests == []
    assert any(issue.code == "worker_replan_signal_rejected" for issue in result.issues)


def test_worker_runtime_accepts_planner_owned_replan_signal(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "finalize",
                    "phase": "FINALIZE",
                    "goal": "Finalize.",
                    "instructions": ["Signal true semantic drift."],
                    "input_artifacts": [],
                    "output_artifacts": ["final_report"],
                    "allowed_tool_groups": [],
                    "max_tool_calls": 0,
                    "max_model_calls": 1,
                }
            ],
            "artifact_contracts": [{"id": "final_report"}],
        }
    )
    replacement_plan = plan.model_copy(update={"plan_id": "v2_plan_signal_replanned"})
    planner = ReplanRecorder(replacement_plan)
    client = QueueClient(
        [
            {
                "planner_replan_signal": {
                    "reason": "repo state contradicts planner assumption",
                    "phase_id": "finalize",
                    "issue_codes": ["repo_plan_drift"],
                    "recommended_action": "rebuild finalize phase",
                }
            },
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "finished after semantic replan",
                    "artifacts": [
                        {
                            "id": "final_report",
                            "kind": "final_report",
                            "content": {"summary": "done"},
                            "producer": "worker",
                        }
                    ],
                }
            },
        ]
    )

    result = WorkerRuntime(model_client=client, root_path=tmp_path, planner_runtime=planner).run(plan, envelope=_envelope())

    assert result.status == "completed"
    assert result.plan_id == "v2_plan_signal_replanned"
    assert len(planner.requests) == 1
    assert planner.requests[0].issues[0].code == "repo_plan_drift"
