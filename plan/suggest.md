Updated Phase 1 architecture
User Input
  ↓
[Decompressor Runtime]
  internal prompt chain:
    1. Normalize request
    2. Extract artifacts
    3. Classify intent/domain/risk
    4. Infer context requirements
    5. Recommend planner
    6. Assign budget hint
    7. Assemble Envelope
    8. Validate Envelope
  emits Envelope
  ↓
[Planner Runtime]
  uses Envelope
  selects planner
  creates Plan
  ↓
[Worker-Kernel Runtime]
  validates Plan
  enforces budget
  compiles Tasks
  dispatches Workers
  returns Result

This keeps the top-level graph simple:

decompressor_node → planner_node → worker_kernel_node → END

But the decompressor node itself can be powerful.

Correct decompressor design

Use prompt chaining, not one big prompt.

Bad:

raw user input → one giant LLM prompt → Envelope

Better:

raw user input
  ↓
Prompt 1: normalization
  ↓
Prompt 2: intent/domain classification
  ↓
Prompt 3: artifact/context extraction
  ↓
Prompt 4: risk/budget/planner hint
  ↓
Prompt 5: envelope assembly/reconciliation
  ↓
Pydantic validation
  ↓
Envelope

This gives you quality without letting the decompressor become chaotic.

Decompressor internal chain
Chain 1: Request Normalizer

Input:

fix the app

Output:

{
  "normalized_input": "Fix the application.",
  "user_goal": "The user wants the application repaired.",
  "ambiguity": ["No target file, error message, or failing behavior was provided."]
}

Purpose:

Clean the user message.
Preserve intent.
Do not invent missing details.
Chain 2: Artifact Extractor

Input:

fix network_sniffer.py

Output:

{
  "artifacts": [
    {
      "type": "file_hint",
      "path": "network_sniffer.py",
      "language_hint": "python"
    }
  ]
}

For:

fix docker-compose.yml and check nginx.conf

Output:

{
  "artifacts": [
    {
      "type": "file_hint",
      "path": "docker-compose.yml",
      "domain_hint": "infra"
    },
    {
      "type": "file_hint",
      "path": "nginx.conf",
      "domain_hint": "infra"
    }
  ]
}
Chain 3: Intent Classifier

Output:

{
  "input_type": "mutation_request",
  "intents": ["code.fix"],
  "domains": ["code"],
  "confidence": 0.84
}

For vague input:

fix the app

Output:

{
  "input_type": "mutation_request",
  "intents": ["fix.ambiguous", "observe_first"],
  "domains": ["code", "general"],
  "confidence": 0.58
}

This is where LLM helps more than regex.

Chain 4: Risk and Context Classifier

Output:

{
  "risks": [
    "mutation_requested",
    "needs_verification",
    "observation_context_needed"
  ],
  "context_needed": [
    "repo_tree",
    "target_file_or_error_context"
  ],
  "execution_hints": [
    "observe_first_required",
    "do_not_patch_before_observation"
  ]
}

This is important.

For vague mutation requests, the decompressor should strongly tell the planner:

observe first

Not:

patch immediately
Chain 5: Planner Recommendation

The decompressor can recommend the planner.

Add this to Envelope:

planner_hint: str | None = None
planner_confidence: float = 0.0
planner_alternatives: list[str] = []

Example:

{
  "planner_hint": "code_planner",
  "planner_confidence": 0.86,
  "planner_alternatives": ["fallback_planner"]
}

For:

what is docker
{
  "planner_hint": "direct_planner",
  "planner_confidence": 0.91,
  "planner_alternatives": ["infra_planner"]
}

For:

fix terraform apply error
{
  "planner_hint": "infra_planner",
  "planner_confidence": 0.88,
  "planner_alternatives": ["code_planner", "fallback_planner"]
}

This makes PlannerRuntime easier.

Planner selector can mostly follow the decompressor’s hint.

Updated Envelope schema

I would slightly expand Envelope.

Not too much. Just enough.

from pydantic import BaseModel, Field
from typing import Any


class Envelope(BaseModel):
    request_id: str

    raw_input: str
    normalized_input: str
    user_goal: str | None = None

    input_type: str
    intents: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    context_needed: list[str] = Field(default_factory=list)
    execution_hints: list[str] = Field(default_factory=list)

    planner_hint: str | None = None
    planner_confidence: float = 0.0
    planner_alternatives: list[str] = Field(default_factory=list)

    budget_hint: str = "medium"
    confidence: float = 0.0

    ambiguity: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)

This is still one object.

No schema explosion.

Key design correction

The planner should not be responsible for discovering the user intent from scratch.

The planner should receive:

rich Envelope

Then create:

Plan

So PlannerRuntime becomes:

Envelope → Plan

Not:

raw user input → understand everything → Plan

That is exactly why your decompressor should be strong.

Decompressor authority level

Give decompressor authority over:

normalization
classification
artifact hints
risk hints
context requirements
planner recommendation
budget hint
ambiguity detection

Do not give decompressor authority over:

worker steps
tool choices
task dispatch
budget enforcement
file mutation
retry decisions

That keeps the design clean.

Best runtime split now
DecompressorRuntime
  = LLM-heavy understanding layer

PlannerRuntime
  = LLM-heavy strategy layer

WorkerKernelRuntime
  = deterministic execution layer

More precise:

Decompressor: "What does the user mean?"
Planner:      "What should be done?"
Kernel:       "How do we safely execute the plan?"
Worker:       "Do this bounded task."
Prompt-chain decompressor skeleton
class DecompressorRuntime:
    def __init__(self, model, validator):
        self.model = model
        self.validator = validator

    def run(self, user_input: str) -> Envelope:
        normalized = self.normalize(user_input)

        artifacts = self.extract_artifacts(
            raw_input=user_input,
            normalized=normalized,
        )

        classification = self.classify_request(
            raw_input=user_input,
            normalized=normalized,
            artifacts=artifacts,
        )

        context = self.infer_context_and_risk(
            raw_input=user_input,
            normalized=normalized,
            artifacts=artifacts,
            classification=classification,
        )

        planner_hint = self.recommend_planner(
            classification=classification,
            artifacts=artifacts,
            context=context,
        )

        envelope = self.assemble_envelope(
            raw_input=user_input,
            normalized=normalized,
            artifacts=artifacts,
            classification=classification,
            context=context,
            planner_hint=planner_hint,
        )

        return self.validator.validate_or_repair(envelope)

This is the pattern.

Deterministic logic still exists, but as guardrail

Even if decompressor is LLM-heavy, keep deterministic code for:

Pydantic validation
allowed label checking
empty input handling
request_id creation
schema repair retry
confidence threshold
fallback if LLM fails

So the LLM decides classification, but deterministic code controls the format.

Correct pattern:

LLM proposes Envelope
Validator accepts/rejects/repairs

Not:

LLM outputs whatever and planner adapts

That would rot the system.

PlannerRuntime with decompressor hint

Planner selector should become simple:

class PlannerRuntime:
    def run(self, envelope: Envelope) -> Plan:
        planner = self.selector.select(envelope)
        return planner.create_plan(envelope)

Selector:

class PlannerSelector:
    def select(self, envelope: Envelope):
        if envelope.planner_hint and envelope.planner_confidence >= 0.70:
            return self.registry.get(envelope.planner_hint)

        if "observe_first" in envelope.intents:
            return self.registry.get("fallback_planner")

        if "infra" in envelope.domains:
            return self.registry.get("infra_planner")

        if "code" in envelope.domains:
            return self.registry.get("code_planner")

        if envelope.input_type == "question":
            return self.registry.get("direct_planner")

        return self.registry.get("fallback_planner")

So decompressor leads. PlannerRuntime only safeguards.

Example: fix the app

Decompressor should produce:

{
  "request_id": "req-0001",
  "raw_input": "fix the app",
  "normalized_input": "Fix the application.",
  "user_goal": "Repair the application, but the specific failure is not provided.",
  "input_type": "mutation_request",
  "intents": ["fix.ambiguous", "observe_first"],
  "domains": ["code"],
  "risks": [
    "mutation_requested",
    "ambiguous_mutation",
    "needs_verification",
    "observation_context_needed"
  ],
  "artifacts": [],
  "context_needed": ["repo_tree", "error_context", "target_files_unknown"],
  "execution_hints": [
    "observe_first_required",
    "do_not_patch_before_observation"
  ],
  "planner_hint": "fallback_planner",
  "planner_confidence": 0.74,
  "planner_alternatives": ["code_planner"],
  "budget_hint": "medium",
  "confidence": 0.62,
  "ambiguity": [
    "No error message was provided.",
    "No target file was provided.",
    "No failing behavior was described."
  ],
  "assumptions": [
    "The request likely refers to the current repository or application workspace."
  ]
}

Then planner creates observe-first plan.

Example: fix network_sniffer.py
{
  "request_id": "req-0002",
  "raw_input": "fix network_sniffer.py",
  "normalized_input": "Fix network_sniffer.py.",
  "user_goal": "Repair the target Python file.",
  "input_type": "mutation_request",
  "intents": ["code.fix"],
  "domains": ["code"],
  "risks": [
    "mutation_requested",
    "file_mutation",
    "needs_verification"
  ],
  "artifacts": [
    {
      "type": "file_hint",
      "path": "network_sniffer.py",
      "language_hint": "python"
    }
  ],
  "context_needed": ["repo_tree", "target_file"],
  "execution_hints": [
    "inspect_target_file_before_patch",
    "verify_after_patch"
  ],
  "planner_hint": "code_planner",
  "planner_confidence": 0.91,
  "planner_alternatives": ["fallback_planner"],
  "budget_hint": "medium",
  "confidence": 0.86,
  "ambiguity": [
    "No concrete bug description was provided."
  ],
  "assumptions": [
    "The target file exists in the current workspace."
  ]
}

This is exactly the kind of output that makes planner strong.