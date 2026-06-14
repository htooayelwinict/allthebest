from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ToolArgs(BaseModel):
    code: str
    save_as: str | None = None
    timeout: int = Field(default=90, ge=1, le=600)

    @field_validator("code")
    @classmethod
    def strip_code_fence(cls, value: str) -> str:
        text = value.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:typescript|ts|javascript|js)?", "", text, flags=re.I).strip()
            text = re.sub(r"```$", "", text).strip()
        if not text:
            raise ValueError("code cannot be empty")
        return text

    @field_validator("save_as")
    @classmethod
    def clean_save_as(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("._-")
        return cleaned or None


class ToolCall(BaseModel):
    id: str = ""
    name: Literal["run_playwright_code"]
    args: ToolArgs


class SourceRef(BaseModel):
    title: str = ""
    url: str = ""
    note: str = ""


class FinalResult(BaseModel):
    summary: str = ""
    key_findings: list[str] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    related_sources: list[SourceRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class AgentDecision(BaseModel):
    status: Literal["tool_calls", "final"]
    tool_calls: list[ToolCall] = Field(default_factory=list)
    final_result: FinalResult | None = None
    notes: str = ""

    @field_validator("tool_calls", mode="before")
    @classmethod
    def coerce_tool_calls(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        return value if isinstance(value, list) else []


@dataclass(frozen=True)
class ValidationIssue:
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.detail}"


@dataclass
class PlaywrightResult:
    ok: bool
    execution_ok: bool
    data_ok: bool
    warnings: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    test_file: str | None
    saved: bool
    workspace: str
    parsed_json: Any = None
    setup_error: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "execution_ok": self.execution_ok,
            "data_ok": self.data_ok,
            "warnings": self.warnings,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "test_file": self.test_file,
            "saved": self.saved,
            "workspace": self.workspace,
            "parsed_json": self.parsed_json,
            "setup_error": self.setup_error,
        }


@dataclass
class ToolRunRecord:
    id: str
    name: str
    args: dict[str, Any]
    result: dict[str, Any]
