"""Deterministic decomposer canonicalization and exact literal extraction."""

from __future__ import annotations

import re
from typing import Any

from appV2.schemas import Envelope, ExactLiteral


PATH_RE = re.compile(r"(?<![\w.-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+(?:\.[A-Za-z0-9_.-]+)?")
JSON_KEY_RE = re.compile(r"\b[a-z][a-z0-9_]{2,}\b")
GENERATED_PLACEHOLDER_RE = re.compile(r"\[[A-Z][A-Z0-9_]{1,}\]")
GENERIC_INPUT_TYPES = {"request", "task", "input", "payload", "unknown", "general", "other"}


def extract_literal_contract(text: str) -> list[ExactLiteral]:
    literals: dict[str, ExactLiteral] = {}
    for match in PATH_RE.finditer(text or ""):
        value = match.group(0).strip(".,;:)")
        literals[value] = ExactLiteral(value=value, kind="path", source="user_input")

    for token in JSON_KEY_RE.findall(text or ""):
        if "_" not in token:
            continue
        if token in {"this_repo", "current_file"}:
            continue
        literals.setdefault(token, ExactLiteral(value=token, kind="json_key", source="user_input"))
    return list(literals.values())


def canonicalize_envelope(envelope: Envelope) -> Envelope:
    literals = {literal.value: literal for literal in envelope.literal_contract if not _is_generated_placeholder(literal.value)}
    for literal in extract_literal_contract(envelope.raw_input):
        literals.setdefault(literal.value, literal)

    metadata = dict(envelope.metadata)
    placeholders = sorted(_find_generated_placeholders(envelope.model_dump(mode="json")))
    if placeholders:
        metadata["invalid_generated_placeholders"] = placeholders

    normalized_input = _strip_placeholders(envelope.normalized_input)
    artifacts = _strip_placeholders_from_value(envelope.artifacts)
    constraints = _strip_placeholders_from_value(envelope.constraints)
    input_type = envelope.input_type.strip()
    if input_type.lower() in GENERIC_INPUT_TYPES:
        input_type = "ambiguous_request"

    return envelope.model_copy(
        update={
            "normalized_input": normalized_input,
            "input_type": input_type,
            "artifacts": artifacts,
            "constraints": constraints,
            "literal_contract": list(literals.values()),
            "metadata": metadata,
        }
    )


def _find_generated_placeholders(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(GENERATED_PLACEHOLDER_RE.findall(value))
    if isinstance(value, dict):
        found: set[str] = set()
        for nested in value.values():
            found.update(_find_generated_placeholders(nested))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for nested in value:
            found.update(_find_generated_placeholders(nested))
        return found
    return set()


def _strip_placeholders_from_value(value: Any) -> Any:
    if isinstance(value, str):
        return _strip_placeholders(value)
    if isinstance(value, dict):
        return {key: _strip_placeholders_from_value(nested) for key, nested in value.items() if not _is_generated_placeholder(str(nested))}
    if isinstance(value, list):
        return [
            _strip_placeholders_from_value(nested)
            for nested in value
            if not (isinstance(nested, str) and _is_generated_placeholder(nested))
        ]
    return value


def _strip_placeholders(value: str) -> str:
    return " ".join(GENERATED_PLACEHOLDER_RE.sub("", value or "").split())


def _is_generated_placeholder(value: str) -> bool:
    return bool(GENERATED_PLACEHOLDER_RE.fullmatch((value or "").strip()))
