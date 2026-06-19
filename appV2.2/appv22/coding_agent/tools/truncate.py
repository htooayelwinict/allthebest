"""Output truncation. Port of pi/packages/coding-agent/src/core/tools/truncate.ts."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024  # 51200


@dataclass
class TruncationResult:
    content: str
    truncated: bool
    truncated_by: str | None  # "lines" | "bytes" | None
    output_lines: int
    total_lines: int
    first_line_exceeds_limit: bool
    max_lines: int = DEFAULT_MAX_LINES
    max_bytes: int = DEFAULT_MAX_BYTES


def format_size(num_bytes: int) -> str:
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.0f}KB"
    return f"{num_bytes}B"


def truncate_head(content: str, max_lines: int = DEFAULT_MAX_LINES, max_bytes: int = DEFAULT_MAX_BYTES) -> TruncationResult:
    """Keep the head of `content` within line and byte limits (pi semantics)."""
    lines = content.split("\n")
    total_lines = len(lines)

    if lines and len(lines[0].encode("utf-8")) > max_bytes:
        return TruncationResult(
            content="",
            truncated=True,
            truncated_by="bytes",
            output_lines=0,
            total_lines=total_lines,
            first_line_exceeds_limit=True,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    kept: list[str] = []
    byte_count = 0
    truncated_by: str | None = None
    for index, line in enumerate(lines):
        if index >= max_lines:
            truncated_by = "lines"
            break
        line_bytes = len(line.encode("utf-8")) + (1 if index > 0 else 0)
        if byte_count + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        kept.append(line)
        byte_count += line_bytes

    truncated = truncated_by is not None
    return TruncationResult(
        content="\n".join(kept),
        truncated=truncated,
        truncated_by=truncated_by,
        output_lines=len(kept),
        total_lines=total_lines,
        first_line_exceeds_limit=False,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )
