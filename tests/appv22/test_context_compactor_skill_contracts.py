from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "appV2.2"))

from appv22.context.compressor import AgentContextCompressor


def test_dual_compaction_preserves_skill_prompt_instructions():
    messages = [
        {"role": "system", "content": "runtime contract"},
        {
            "role": "system",
            "name": "provider_context_section",
            "section": "skills",
            "payload": [
                {
                    "skill_id": "demo.web_research",
                    "extension_id": "demo",
                    "summary": "Research public sources.",
                    "tool_ids": ("demo.search",),
                    "observation_contract": {"evidence_refs": ("world://search/latest",)},
                    "instructions": (
                        "Use the skill prompt as the domain adapter.",
                        "Rehydrate exact evidence before final claims.",
                    ),
                }
            ],
            "content": "skills: verbose",
        },
        {"role": "tool", "content": "x" * 5000, "tool_result_id": "tool_1"},
        {"role": "user", "content": "continue"},
    ]

    compacted = AgentContextCompressor(max_chars=1600, threshold=0.10).compress(
        messages,
        previous_summary={},
    )

    skill_sections = [
        message for message in compacted
        if message.get("name") == "provider_context_section" and message.get("section") == "skills"
    ]
    assert skill_sections
    assert skill_sections[0]["payload"][0]["instructions"] == (
        "Use the skill prompt as the domain adapter.",
        "Rehydrate exact evidence before final claims.",
    )


def test_dual_compaction_preserves_world_evidence_refs():
    messages = [
        {"role": "system", "content": "runtime contract"},
        {
            "role": "system",
            "name": "provider_context_section",
            "section": "world",
            "payload": {
                "world_refs": {
                    "world://repo_snapshot/latest": {
                        "ref_id": "world://repo_snapshot/latest",
                        "kind": "file_management.repo_snapshot",
                        "summary": "repo snapshot with office evidence",
                        "payload": {
                            "files": ["docs/context.md"],
                            "text_previews": {"docs/context.md": "office lease hybrid vendor budget owner risk"},
                            "directories": ["docs"],
                            "errors": [],
                        },
                    }
                }
            },
            "content": "world: verbose",
        },
        {"role": "tool", "content": "x" * 5000, "tool_result_id": "tool_1"},
        {"role": "user", "content": "continue"},
    ]

    compacted = AgentContextCompressor(max_chars=1800, threshold=0.10).compress(
        messages,
        previous_summary={},
    )

    world_sections = [
        message for message in compacted
        if message.get("name") == "provider_context_section" and message.get("section") == "world"
    ]
    summaries = [message for message in compacted if message.get("name") == "context_summary"]
    assert world_sections
    assert world_sections[0]["payload"]["world_refs"]["world://repo_snapshot/latest"]["kind"] == (
        "file_management.repo_snapshot"
    )
    assert world_sections[0]["payload"]["world_refs"]["world://repo_snapshot/latest"]["payload"]["text_previews"] == {
        "docs/context.md": "office lease hybrid vendor budget owner risk"
    }
    assert summaries
    assert "world://repo_snapshot/latest" in summaries[0]["summary"]["evidence_refs"]
    assert "Available evidence_refs: world://repo_snapshot/latest" in summaries[0]["content"]
    assert "file_management.repo_snapshot" in summaries[0]["content"]


def test_dual_compaction_never_truncates_evidence_ref_pointers():
    long_ref = "world://file_management.repo_snapshot/latest"
    messages = [
        {"role": "system", "content": "runtime contract"},
        {
            "role": "system",
            "name": "provider_context_section",
            "section": "world",
            "payload": {
                "world_refs": {
                    long_ref: {
                        "ref_id": long_ref,
                        "kind": "file_management.repo_snapshot",
                        "summary": "repo snapshot",
                        "payload": {"files": [f"incoming/noise-{index}.md" for index in range(200)]},
                    }
                }
            },
            "content": "world: verbose " + ("x" * 8000),
        },
        {"role": "tool", "content": "x" * 8000, "tool_result_id": "tool_1"},
        {"role": "user", "content": "continue"},
    ]

    compacted = AgentContextCompressor(max_chars=900, threshold=0.10).compress(
        messages,
        previous_summary={},
    )

    summaries = [message for message in compacted if message.get("name") == "context_summary"]
    assert summaries
    evidence_refs = summaries[0]["summary"]["evidence_refs"]
    assert long_ref in evidence_refs
    assert "world://file_management.repo_snapshot/la" not in evidence_refs


def test_dual_compaction_carries_repo_shape_without_raw_noise():
    ref = "world://file_management.repo_snapshot/latest"
    raw_marker = "RAW_NOISE_SENTINEL"
    files = [
        "README.md",
        "docs/context.md",
        "notes/handoff.md",
        *[f"incoming/{raw_marker}_{index:03d}.md" for index in range(80)],
    ]
    messages = [
        {"role": "system", "content": "runtime contract"},
        {
            "role": "system",
            "name": "provider_context_section",
            "section": "world",
            "payload": {
                "world_refs": {
                    ref: {
                        "ref_id": ref,
                        "kind": "file_management.repo_snapshot",
                        "summary": "repo snapshot",
                        "payload": {
                            "files": files,
                            "directories": ["docs", "notes", "incoming"],
                            "text_previews": {
                                "docs/context.md": "important project context",
                                f"incoming/{raw_marker}_000.md": "noise",
                            },
                        },
                    }
                }
            },
            "content": "world: verbose " + (" ".join(files) * 10),
        },
        {"role": "tool", "content": "x" * 8000, "tool_result_id": "tool_1"},
        {"role": "user", "content": "continue"},
    ]

    compacted = AgentContextCompressor(max_chars=2200, threshold=0.10).compress(
        messages,
        previous_summary={},
    )

    world_sections = [
        message for message in compacted
        if message.get("name") == "provider_context_section" and message.get("section") == "world"
    ]
    assert world_sections
    payload = world_sections[0]["payload"]["world_refs"][ref]["payload"]
    assert payload["file_count"] == 83
    assert payload["top_level_file_groups"]["incoming"] == 80
    assert "README.md" in payload["important_files"]


def test_hard_budget_compaction_preserves_late_selected_action_tool_contract():
    target_tool = "toolscale.zz_finalize_assessment"
    selected_tools = [f"toolscale.decoy_{index:02d}" for index in range(16)] + [target_tool]
    tool_definitions = [
        {
            "tool_id": tool_id,
            "category": "act" if tool_id == target_tool else "observe",
            "risk_level": "medium" if tool_id == target_tool else "low",
            "argument_schema": {
                "type": "object",
                "properties": {"assessment_id": {"type": "string"}, "summary": {"type": "string"}},
                "required": ["assessment_id", "summary"],
            },
            "guidance": "Finalize the assessment with SCALE-244 Harbor routing 91%."
            if tool_id == target_tool
            else "Decoy tool; not needed.",
        }
        for tool_id in selected_tools
    ]
    messages = [
        {"role": "system", "content": "runtime contract"},
        {
            "role": "system",
            "name": "provider_context_section",
            "section": "skills",
            "payload": [
                {
                    "skill_id": "toolscale.large_toolset",
                    "extension_id": "toolscale",
                    "summary": "Large selected toolset.",
                    "tool_ids": tuple(selected_tools),
                    "instructions": ("Use only toolscale.zz_finalize_assessment.",),
                }
            ],
            "content": "skills: " + ("noise " * 800),
        },
        {
            "role": "system",
            "name": "provider_context_section",
            "section": "tools",
            "payload": selected_tools,
            "content": "tools: " + ("noise " * 800),
        },
        {
            "role": "system",
            "name": "provider_context_section",
            "section": "tool_definitions",
            "payload": tool_definitions,
            "content": "tool_definitions: " + ("noise " * 800),
        },
        {
            "role": "system",
            "name": "provider_context_section",
            "section": "selection",
            "payload": {
                "mode": "THINK",
                "selected_tools": selected_tools,
                "selected_skills": ["toolscale.large_toolset"],
            },
            "content": "selection: " + ("noise " * 800),
        },
        {"role": "tool", "content": "x" * 8000, "tool_result_id": "tool_1"},
        {"role": "user", "content": "continue after denial"},
    ]

    compacted = AgentContextCompressor(max_chars=2200, threshold=0.10).compress(
        messages,
        previous_summary={
            "open_risks": [
                "toolscale.zz_finalize_assessment was denied; retry with SCALE-244 and required terms."
            ]
        },
    )

    assert target_tool in str(compacted)
    tool_definition_sections = [
        message
        for message in compacted
        if message.get("name") == "provider_context_section" and message.get("section") == "tool_definitions"
    ]
    assert tool_definition_sections
    assert target_tool in [tool["tool_id"] for tool in tool_definition_sections[0]["payload"]]


def test_hard_budget_compaction_preserves_latest_user_task_head_when_oversized():
    required_head = (
        "Toolscale large toolset stress: produce the Harbor routing assessment for SCALE-244. "
        "Required facts: Harbor routing is 91% ready; recommendation is enable staged release; "
        "next action is notify routing owner."
    )
    messages = [
        {"role": "system", "content": "runtime contract"},
        {
            "role": "system",
            "name": "provider_context_section",
            "section": "state",
            "payload": {"mode": "THINK", "context_summary": {}},
            "content": "state: " + ("noise " * 500),
        },
        {
            "role": "user",
            "name": "user_goal",
            "content": required_head + "\n" + ("stale noisy line tok_private should not matter\n" * 900),
        },
    ]

    compacted = AgentContextCompressor(max_chars=900, threshold=0.10).compress(
        messages,
        previous_summary={},
    )

    compacted_text = str(compacted)
    assert "user_goal" in compacted_text
    assert "SCALE-244" in compacted_text
    assert "Harbor routing" in compacted_text
    assert "enable staged release" in compacted_text
    assert "notify routing owner" in compacted_text


def test_hard_budget_compaction_keeps_office_story_world_payload_details():
    ref = "world://file_management.repo_snapshot/latest"
    messages = [
        {"role": "system", "content": "runtime contract"},
        {
            "role": "system",
            "name": "provider_context_section",
            "section": "world",
            "payload": {
                "world_refs": {
                    ref: {
                        "ref_id": ref,
                        "kind": "file_management.repo_snapshot",
                        "summary": "repo snapshot",
                        "payload": {
                            "files": [
                                "README.md",
                                "docs/project-map.md",
                                "meetings/standup-thursday.md",
                                "inbox/vendor-finch.md",
                                *[f"archive/noise/{index:03d}.md" for index in range(160)],
                            ],
                            "directories": ["docs", "meetings", "inbox", "archive/noise"],
                            "text_previews": {
                                "docs/project-map.md": "Project Maple. Finch owns badges. Atlas ticket AT-481.",
                                "meetings/standup-thursday.md": "Thursday: invoice and badge risks need Rina and Noah.",
                                "inbox/vendor-finch.md": "Finch ETA Thursday 3pm.",
                                "archive/noise/000.md": "noise",
                            },
                            "errors": [],
                        },
                    }
                }
            },
            "content": "world: verbose " + ("x" * 9000),
        },
        {"role": "tool", "content": "x" * 9000, "tool_result_id": "tool_1"},
        {"role": "user", "content": "continue"},
    ]

    compacted = AgentContextCompressor(max_chars=1700, threshold=0.10).compress(
        messages,
        previous_summary={},
    )

    world_sections = [
        message for message in compacted
        if message.get("name") == "provider_context_section" and message.get("section") == "world"
    ]
    assert world_sections
    payload = world_sections[0]["payload"]["world_refs"][ref]["payload"]
    previews = payload["text_previews"]
    assert "docs/project-map.md" in previews
    assert "meetings/standup-thursday.md" in previews
    assert "inbox/vendor-finch.md" in previews
    assert "archive/noise/000.md" not in previews
    assert "docs/project-map.md" in payload["important_files"]
    assert "meetings/standup-thursday.md" in payload["important_files"]
    assert "inbox/vendor-finch.md" in payload["important_files"]
    compacted_text = repr(compacted)
    assert "archive/noise/000.md" not in compacted_text


def test_dual_compaction_preserves_unknown_world_payload_generically():
    messages = [
        {"role": "system", "content": "runtime contract"},
        {
            "role": "system",
            "name": "provider_context_section",
            "section": "world",
            "payload": {
                "world_refs": {
                    "world://web_research.search/abc": {
                        "ref_id": "world://web_research.search/abc",
                        "kind": "web_research.search",
                        "summary": "search results for vendor risk",
                        "payload": {
                            "query": "vendor risk",
                            "results": [
                                {
                                    "title": "BadgeCo risk review",
                                    "url": "https://example.test/badgeco",
                                    "snippet": "BadgeCo missed delivery twice.",
                                }
                            ],
                        },
                    }
                }
            },
            "content": "world: verbose",
        },
        {"role": "tool", "content": "x" * 5000, "tool_result_id": "tool_1"},
        {"role": "user", "content": "continue"},
    ]

    compacted = AgentContextCompressor(max_chars=1800, threshold=0.10).compress(
        messages,
        previous_summary={},
    )

    world_section = next(
        message for message in compacted
        if message.get("name") == "provider_context_section" and message.get("section") == "world"
    )
    ref = world_section["payload"]["world_refs"]["world://web_research.search/abc"]
    assert ref["kind"] == "web_research.search"
    assert ref["payload"]["query"] == "vendor risk"
    assert "BadgeCo risk review" in str(ref["payload"])
