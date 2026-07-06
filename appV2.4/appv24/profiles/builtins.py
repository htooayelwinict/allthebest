"""Built-in appv24 profiles.

These profiles are intentionally static. v1 does not load user, project, package,
or environment-defined profiles.
"""

from __future__ import annotations

from appv24.profiles.types import AppProfile
from appv24.workflows.prompt_fragments import (
    render_capability_catalog_fragment,
    render_required_package_blocks_fragment,
    render_schema_reminders_fragment,
    render_side_effect_contract_fragment,
)

DEFAULT_PROFILE_ID = "coding"
READ_ONLY_TOOLS = ("read", "grep", "find", "ls")
MUTATING_TOOLS = ("bash", "edit", "write")

_READ_ONLY_APPEND = (
    "Profile: read-only.\n"
    "Inspect and explain the workspace using only read-only tools. "
    "Do not edit, write, delete, move, or create files. "
    "Do not run shell commands."
)

_PLANNING_APPEND = (
    "Profile: planning.\n"
    "Create careful implementation plans from repository evidence. "
    "Do not edit, write, delete, move, or create files. "
    "When the work needs implementation, stop with a clear plan and wait for approval."
)

_RESEARCH_LITE_APPEND = (
    "Profile: research-lite.\n"
    "Use local repository files as the evidence base. Summarize findings, cite paths, "
    "and separate facts from inference. Do not modify files or run shell commands."
)

_SRE_LITE_APPEND = (
    "Profile: sre-lite.\n"
    "Inspect infrastructure, deployment, configuration, and operational files using "
    "read-only tools. Produce incident notes, risk summaries, or runbook drafts in the "
    "final response only. Do not modify files or run shell commands."
)

_SECURITY_LAB_LITE_APPEND = (
    "Profile: security-lab-lite.\n"
    "Review local code and configuration defensively. Focus on attack surface, likely "
    "risks, mitigations, and verification steps. Do not exploit systems, modify files, "
    "or run shell commands."
)

_WORKFLOW_DESIGNER_LITE_APPEND = " ".join(
    (
        "Profile: workflow-designer-lite.",
        "Design safe, reviewable workflow-as-code designs from natural language.",
        "Treat every workflow as a passive design artifact, not executable work.",
        "Use local repository files only as evidence when needed.",
        "Produce the workflow design in the final response only.",
        "appv24 may ask for staged workflow compiler passes.",
        "When a stage prompt asks for intent or skeleton, answer only that stage.",
        "Only the package stage should emit labeled fenced code blocks.",
        "Do not edit, write, delete, move, or create files.",
        "Do not run shell commands.",
        "Do not run workflows.",
        "Do not call external APIs.",
        "Do not request raw secrets, send messages, publish, provision cloud resources, or mutate external systems.",
        "Structure the response with workflow summary, assumptions, approval gates, test plan, risk review, future runner target, and artifact drafts.",
        render_required_package_blocks_fragment(),
        "Use these appv24 schema shapes exactly.",
        render_schema_reminders_fragment(),
        render_capability_catalog_fragment(),
        render_side_effect_contract_fragment(),
        "Connector targets may include MCP, direct API, OpenAPI, LangGraph, n8n or Pipedream export, and IaC, but only as design targets.",
    )
)

BUILTIN_PROFILES: tuple[AppProfile, ...] = (
    AppProfile(
        id="coding",
        display_name="Coding",
        description="Default appv24 coding agent behavior.",
    ),
    AppProfile(
        id="read-only",
        display_name="Read Only",
        description="Inspect and explain files without mutation.",
        allowed_tool_names=READ_ONLY_TOOLS,
        excluded_tool_names=MUTATING_TOOLS,
        append_system_prompt=_READ_ONLY_APPEND,
        max_iterations=40,
        tool_loop_guardrails={"hard_stop_enabled": True},
    ),
    AppProfile(
        id="planning",
        display_name="Planning",
        description="Read-only planning mode for implementation design.",
        allowed_tool_names=READ_ONLY_TOOLS,
        excluded_tool_names=MUTATING_TOOLS,
        append_system_prompt=_PLANNING_APPEND,
        max_iterations=60,
        tool_loop_guardrails={"hard_stop_enabled": True},
    ),
    AppProfile(
        id="research-lite",
        display_name="Research Lite",
        description="Local evidence research without workspace mutation.",
        allowed_tool_names=READ_ONLY_TOOLS,
        excluded_tool_names=MUTATING_TOOLS,
        append_system_prompt=_RESEARCH_LITE_APPEND,
        max_iterations=50,
        tool_loop_guardrails={"hard_stop_enabled": True},
    ),
    AppProfile(
        id="sre-lite",
        display_name="SRE Lite",
        description="Read-only operational and infrastructure analysis.",
        allowed_tool_names=READ_ONLY_TOOLS,
        excluded_tool_names=MUTATING_TOOLS,
        append_system_prompt=_SRE_LITE_APPEND,
        max_iterations=50,
        tool_loop_guardrails={"hard_stop_enabled": True},
    ),
    AppProfile(
        id="security-lab-lite",
        display_name="Security Lab Lite",
        description="Defensive local code and configuration review.",
        allowed_tool_names=READ_ONLY_TOOLS,
        excluded_tool_names=MUTATING_TOOLS,
        append_system_prompt=_SECURITY_LAB_LITE_APPEND,
        max_iterations=50,
        tool_loop_guardrails={"hard_stop_enabled": True},
    ),
    AppProfile(
        id="workflow-designer-lite",
        display_name="Workflow Designer Lite",
        description="Passive natural-language workflow package designer.",
        allowed_tool_names=READ_ONLY_TOOLS,
        excluded_tool_names=MUTATING_TOOLS,
        append_system_prompt=_WORKFLOW_DESIGNER_LITE_APPEND,
        max_iterations=60,
        tool_loop_guardrails={"hard_stop_enabled": True},
    ),
)
