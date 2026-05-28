"""Deterministic prompt-chain style decomposition into `Envelope` objects."""

from __future__ import annotations

import itertools
import re
from typing import Any

from app.decompressor.contracts import PromptChainModelClient
from app.decompressor.env_config import build_decompressor_model_client
from app.decompressor.labels import PLANNER_HINTS
from app.decompressor.prompt_chain import LLMPromptChainDecompressor
from app.schemas import Envelope


_REQUEST_COUNTER = itertools.count(1)


class DecompressorRuntime:
    """Classifies plain user text through focused, validated chain stages.

    With no constructor arguments, the runtime is fully deterministic. Passing a
    `model_client` enables an optional internal LLM prompt chain; provider
    clients only need to implement `PromptChainModelClient.complete_json`. Any
    model-call, JSON, schema, or label failure falls back to the deterministic
    path for the same request ID. The decompressor may enrich hints, but it still
    never creates plan steps, dispatches workers, or enforces budgets.
    """

    _QUESTION_PREFIXES = ("what", "why", "how", "when", "where", "who")
    _RESEARCH_HINTS = ("research", "investigate", "compare", "summarize", "analyze")
    _INFRA_HINTS = (
        "docker",
        "kubernetes",
        "k8s",
        "terraform",
        "helm",
        "cloud",
        "ci",
        "cd",
        "pipeline",
        "deploy",
        "deployment",
        "shell",
        "debug",
    )
    _CODE_MUTATION_HINTS = ("fix", "patch", "edit", "change", "update", "refactor")
    def __init__(
        self,
        model_client: PromptChainModelClient | None = None,
        prompt_chain: Any | None = None,
    ) -> None:
        if prompt_chain is not None:
            self._prompt_chain = prompt_chain
        elif model_client is not None:
            self._prompt_chain = LLMPromptChainDecompressor(
                model_client=model_client,
                deterministic_fallback=self._run_deterministic,
            )
        else:
            self._prompt_chain = None

    @classmethod
    def from_env(cls, dotenv_path: str = ".env", **client_options: Any) -> "DecompressorRuntime":
        """Create a runtime from `.env` when `DECOMPRESSOR_LLM_ENABLED=true`.

        Missing or disabled env config returns the deterministic runtime. Enabled
        config builds an OpenAI-compatible JSON client without storing API keys
        or prompts in runtime metadata.
        """

        model_client = build_decompressor_model_client(dotenv_path, **client_options)
        return cls(model_client=model_client)

    def run(self, user_input: str) -> Envelope:
        raw_input = user_input or ""
        request_id = f"req_{next(_REQUEST_COUNTER):03d}"

        if self._prompt_chain is not None:
            return self._prompt_chain.run(raw_input, request_id)

        return self._run_deterministic(raw_input, request_id)

    def _run_deterministic(self, raw_input: str, request_id: str) -> Envelope:
        normalized = self._normalize_request(raw_input)
        artifacts = self._extract_artifacts(normalized["normalized_input"])
        classification = self._classify_request(normalized["normalized_input"], artifacts)
        context = self._infer_context_and_risk(classification, artifacts)
        planner = self._recommend_planner(classification, context)

        envelope = Envelope(
            request_id=request_id,
            raw_input=raw_input,
            normalized_input=normalized["normalized_input"],
            user_goal=normalized["user_goal"],
            input_type=classification["input_type"],
            intents=classification["intents"],
            domains=classification["domains"],
            risks=context["risks"],
            artifacts=artifacts,
            context_needed=context["context_needed"],
            execution_hints=context["execution_hints"],
            planner_hint=planner["planner_hint"],
            planner_confidence=planner["planner_confidence"],
            planner_alternatives=planner["planner_alternatives"],
            budget_hint=classification["budget_hint"],
            confidence=classification["confidence"],
            ambiguity=normalized["ambiguity"] + context["ambiguity"],
            assumptions=normalized["assumptions"],
        )
        return self._validate_envelope(envelope)

    def _normalize_request(self, raw_input: str) -> dict[str, list[str] | str | None]:
        normalized_input = raw_input.strip()
        if not normalized_input:
            return {
                "normalized_input": "",
                "user_goal": None,
                "ambiguity": ["No user input was provided."],
                "assumptions": [],
            }

        lowered = normalized_input.lower()
        ambiguity: list[str] = []
        assumptions: list[str] = []
        if self._is_ambiguous_mutation(lowered, []):
            ambiguity.extend(
                [
                    "No error message was provided.",
                    "No target file was provided.",
                    "No failing behavior was described.",
                ]
            )
            assumptions.append("The request likely refers to the current repository or application workspace.")

        return {
            "normalized_input": normalized_input,
            "user_goal": self._user_goal(normalized_input),
            "ambiguity": ambiguity,
            "assumptions": assumptions,
        }

    def _extract_artifacts(self, text: str) -> list[dict[str, str]]:
        matches = re.findall(r"[\w./-]+\.[A-Za-z0-9]+", text)
        artifacts: list[dict[str, str]] = []
        for path in dict.fromkeys(matches):
            artifact = {"type": "file_hint", "path": path}
            language_hint = self._language_hint(path)
            if language_hint:
                artifact["language_hint"] = language_hint
            domain_hint = self._domain_hint(path)
            if domain_hint:
                artifact["domain_hint"] = domain_hint
            artifacts.append(artifact)
        return artifacts

    def _classify_request(
        self, normalized_input: str, artifacts: list[dict[str, str]]
    ) -> dict[str, str | float | list[str]]:
        lowered = normalized_input.lower()
        file_hints = [artifact["path"] for artifact in artifacts]

        input_type = self._input_type(lowered, file_hints)
        intents = self._intents(lowered, input_type, file_hints)
        domains = self._domains(lowered, artifacts, intents)

        return {
            "input_type": input_type,
            "intents": intents,
            "domains": domains,
            "budget_hint": self._budget_hint(input_type, domains),
            "confidence": self._confidence(input_type, file_hints, domains),
        }

    def _infer_context_and_risk(
        self, classification: dict[str, str | float | list[str]], artifacts: list[dict[str, str]]
    ) -> dict[str, list[str]]:
        input_type = str(classification["input_type"])
        intents = list(classification["intents"])
        file_hints = [artifact["path"] for artifact in artifacts]

        risks = self._risks(input_type, intents, file_hints)
        context_needed = self._context_needed(input_type, file_hints)
        execution_hints: list[str] = []
        ambiguity: list[str] = []

        if file_hints:
            execution_hints.extend(["inspect_target_file_before_patch", "verify_after_patch"])
        if input_type == "ambiguous_request" or "observe_first" in intents:
            execution_hints.extend(["observe_first_required", "do_not_patch_before_observation"])
            ambiguity.append("The request does not identify a concrete target or failure.")

        return {
            "risks": list(dict.fromkeys(risks)),
            "context_needed": list(dict.fromkeys(context_needed)),
            "execution_hints": list(dict.fromkeys(execution_hints)),
            "ambiguity": ambiguity,
        }

    def _input_type(self, lowered: str, file_hints: list[str]) -> str:
        if lowered.startswith(self._QUESTION_PREFIXES):
            return "question"
        if any(token in lowered for token in self._CODE_MUTATION_HINTS) or file_hints:
            if self._is_ambiguous_mutation(lowered, file_hints):
                return "ambiguous_request"
            return "mutation_request"
        return "request"

    def _intents(self, lowered: str, input_type: str, file_hints: list[str]) -> list[str]:
        intents: list[str] = []
        if input_type in {"mutation_request", "ambiguous_request"} and (
            file_hints or any(token in lowered for token in self._CODE_MUTATION_HINTS)
        ):
            if file_hints or not any(token in lowered for token in self._INFRA_HINTS):
                intents.append("code.fix")
        if input_type == "ambiguous_request":
            intents.append("observe_first")
        if any(token in lowered for token in self._RESEARCH_HINTS):
            intents.append("research.lookup")
        if any(token in lowered for token in self._INFRA_HINTS):
            intents.append("infra.debug")
        if input_type == "question":
            intents.append("question.answer")
        return intents

    def _domains(self, lowered: str, artifacts: list[dict[str, str]], intents: list[str]) -> list[str]:
        domains: list[str] = []
        file_hints = [artifact["path"] for artifact in artifacts]
        if file_hints or any(intent.startswith("code.") for intent in intents):
            domains.append("code")
        if any(token in lowered for token in self._INFRA_HINTS) or any(
            artifact.get("domain_hint") == "infra" for artifact in artifacts
        ):
            domains.append("infra")
        if any(intent.startswith("research.") for intent in intents):
            domains.append("research")
        if not domains:
            domains.append("general")
        return domains

    def _risks(self, input_type: str, intents: list[str], file_hints: list[str]) -> list[str]:
        risks: list[str] = []
        if any(intent.startswith("code.") for intent in intents):
            risks.append("mutation_requested")
            if file_hints:
                risks.append("file_mutation")
            risks.append("needs_verification")
        if input_type == "ambiguous_request":
            risks.append("ambiguous_scope")
            risks.append("ambiguous_mutation")
            risks.append("observation_context_needed")
        return risks

    def _context_needed(self, input_type: str, file_hints: list[str]) -> list[str]:
        context: list[str] = []
        if file_hints:
            context.extend(["repo_tree", "target_file"])
        if input_type == "ambiguous_request":
            context.extend(["repo_tree", "scope_clarification"])
        return list(dict.fromkeys(context))

    def _budget_hint(self, input_type: str, domains: list[str]) -> str:
        if input_type == "question":
            return "low"
        if "infra" in domains:
            return "high"
        return "medium"

    def _confidence(self, input_type: str, file_hints: list[str], domains: list[str]) -> float:
        if input_type == "ambiguous_request":
            return 0.62
        if input_type == "mutation_request" and file_hints:
            return 0.86
        if input_type == "mutation_request" and "infra" in domains:
            return 0.82
        if input_type == "question":
            return 0.9
        return 0.65

    def _recommend_planner(
        self,
        classification: dict[str, str | float | list[str]],
        context: dict[str, list[str]],
    ) -> dict[str, str | float | list[str]]:
        input_type = str(classification["input_type"])
        intents = list(classification["intents"])
        domains = list(classification["domains"])

        if input_type == "question":
            alternatives = ["infra_planner"] if "infra" in domains else []
            return self._planner_hint("direct_planner", 0.91, alternatives)
        if "observe_first" in intents or "observe_first_required" in context["execution_hints"]:
            return self._planner_hint("fallback_planner", 0.74, ["code_planner"])
        if "infra" in domains and "code" not in domains:
            return self._planner_hint("infra_planner", 0.88, ["code_planner", "fallback_planner"])
        if any(intent.startswith("research.") for intent in intents):
            return self._planner_hint("research_planner", 0.82, ["fallback_planner"])
        if "code" in domains:
            return self._planner_hint("code_planner", 0.91, ["fallback_planner"])
        return self._planner_hint("fallback_planner", 0.6, [])

    def _planner_hint(
        self, planner_hint: str, planner_confidence: float, planner_alternatives: list[str]
    ) -> dict[str, str | float | list[str]]:
        if planner_hint not in PLANNER_HINTS:
            planner_hint = "fallback_planner"
            planner_confidence = 0.0
        return {
            "planner_hint": planner_hint,
            "planner_confidence": planner_confidence,
            "planner_alternatives": [
                alternative for alternative in planner_alternatives if alternative in PLANNER_HINTS
            ],
        }

    def _validate_envelope(self, envelope: Envelope) -> Envelope:
        return Envelope.model_validate(envelope.model_dump())

    def _user_goal(self, normalized_input: str) -> str:
        lowered = normalized_input.lower()
        if self._is_ambiguous_mutation(lowered, []):
            return "Repair the application, but the specific failure is not provided."
        if any(token in lowered for token in self._CODE_MUTATION_HINTS):
            return "Repair or change the requested target safely."
        if lowered.startswith(self._QUESTION_PREFIXES):
            return "Answer the user's question."
        return "Satisfy the user's request."

    def _language_hint(self, path: str) -> str | None:
        extension = path.rsplit(".", 1)[-1].lower()
        return {
            "py": "python",
            "js": "javascript",
            "ts": "typescript",
            "tsx": "typescript",
            "jsx": "javascript",
            "go": "go",
            "rs": "rust",
            "java": "java",
            "rb": "ruby",
            "yml": "yaml",
            "yaml": "yaml",
            "json": "json",
            "toml": "toml",
        }.get(extension)

    def _domain_hint(self, path: str) -> str | None:
        lowered = path.lower()
        if lowered.endswith(("docker-compose.yml", "docker-compose.yaml", "nginx.conf")):
            return "infra"
        if any(part in lowered for part in ("terraform", ".tf", "dockerfile", "kubernetes", "helm")):
            return "infra"
        return None

    def _is_ambiguous_mutation(self, lowered: str, file_hints: list[str]) -> bool:
        if file_hints:
            return False
        return any(
            phrase in lowered
            for phrase in (
                "fix the app",
                "fix app",
                "fix this",
                "fix it",
                "make it work",
            )
        )
