from __future__ import annotations

import json
from typing import Any

from .playwright_runner import PlaywrightToolRunner
from .schemas import ToolCall, ToolRunRecord


class ToolBroker:
    """Dispatch model-requested tools through appV3-owned boundaries."""

    def __init__(self, *, playwright_runner: PlaywrightToolRunner, max_result_chars: int = 12000) -> None:
        self.playwright_runner = playwright_runner
        self.max_result_chars = max_result_chars

    def run_batch(self, calls: list[ToolCall], *, step: int) -> list[ToolRunRecord]:
        records: list[ToolRunRecord] = []
        for index, call in enumerate(calls, start=1):
            if call.name != "run_playwright_code":
                result = {
                    "ok": False,
                    "execution_ok": False,
                    "data_ok": False,
                    "warnings": ["unknown_tool"],
                    "stderr": f"Unknown tool: {call.name}",
                }
            else:
                result = self.playwright_runner.run_playwright_code(call.args).to_json()
            records.append(
                ToolRunRecord(
                    id=call.id or f"step{step}_tool{index}",
                    name=call.name,
                    args=call.args.model_dump(mode="json", exclude={"code"}) | {"code_chars": len(call.args.code)},
                    result=result,
                )
            )
        return records

    def compact_for_model(self, records: list[ToolRunRecord]) -> list[dict[str, Any]]:
        return [
            {
                "id": record.id,
                "name": record.name,
                "args": record.args,
                "result": self._compact_result(record.result),
                "broker_advice": broker_advice(record.result),
            }
            for record in records
        ]

    def _compact_result(self, result: dict[str, Any]) -> dict[str, Any]:
        compact = dict(result)
        for field_name in ("stdout", "stderr"):
            value = str(compact.get(field_name) or "")
            if len(value) > self.max_result_chars:
                compact[field_name] = value[: self.max_result_chars] + "\n<tool_output_truncated>"
        parsed = compact.get("parsed_json")
        if parsed is not None:
            raw = json.dumps(parsed, ensure_ascii=False)
            if len(raw) > self.max_result_chars:
                compact["parsed_json"] = raw[: self.max_result_chars] + "\n<tool_output_truncated>"
        return compact


def broker_advice(result: dict[str, Any]) -> list[str]:
    warnings = set(result.get("warnings") or [])
    advice: list[str] = []
    if "empty_collection_detected" in warnings or "no_results_detected" in warnings:
        advice.append(
            "This tool call collected zero usable web evidence. Do not retry the same search-engine DOM selectors."
        )
        advice.append(
            "Next call must change source class: use direct authoritative URLs, DuckDuckGo/Bing/Brave anchor extraction, or a known docs/homepage path, then scrape page body text."
        )
        advice.append(
            "Generated code should return a non-empty object with sourceLinks/sourcePages/extractiveSummary and should throw or assert when evidence arrays are empty."
        )
    if "execution_timeout" in warnings:
        advice.append("Timeout occurred. Reduce page count, lower waits, and scrape fewer URLs in the next call.")
    if "code_validation_blocked" in warnings:
        advice.append("Generated code failed validation. Use only @playwright/test imports and console.log the result marker.")
    if "missing_result_marker" in warnings:
        advice.append("The test must print exactly one WEB_RESEARCH_RESULT: line followed by JSON.")
    return advice
