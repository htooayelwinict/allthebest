from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from ..constants import RESULT_MARKER
from ..schemas import ValidationIssue

_VALIDATOR_DIR = Path(__file__).parent
_VALIDATOR_SCRIPT = _VALIDATOR_DIR / "typescript_validator.js"
_VALIDATOR_TIMEOUT_SECONDS = 15


def validate_typescript(source: str) -> list[ValidationIssue]:
    """Validate generated TypeScript before it reaches Playwright.

    This is intentionally narrower than Travis-2's AST validator because appV3
    is a standalone script package. It enforces the web_research tool contract
    and blocks common escape hatches.
    """

    issues: list[ValidationIssue] = []
    if not source.strip():
        return [ValidationIssue("empty_code", "Generated code was empty.")]

    required = {
        "playwright_test_import": r"from\s+['\"]@playwright/test['\"]",
        "test_block": r"\btest\s*\(",
        "result_marker": re.escape(RESULT_MARKER),
    }
    for rule, pattern in required.items():
        if not re.search(pattern, source):
            issues.append(ValidationIssue(rule, f"Missing required pattern: {pattern}"))

    banned_patterns = {
        "child_process": r"\bchild_process\b|node:child_process",
        "filesystem": r"from\s+['\"](?:node:)?fs(?:/promises)?['\"]|require\s*\(\s*['\"](?:node:)?fs",
        "network_module": r"from\s+['\"](?:node:)?(?:http|https|net|dgram|dns|tls)['\"]",
        "os_vm_worker": r"from\s+['\"](?:node:)?(?:os|vm|worker_threads)['\"]",
        "process_env": r"process\s*\.\s*env",
        "process_exit": r"process\s*\.\s*exit",
        "eval": r"\beval\s*\(",
        "new_function": r"new\s+Function\s*\(",
        "shell_exec": r"\b(?:exec|execFile|spawn|spawnSync|execSync)\s*\(",
        "dynamic_import": r"\bimport\s*\(",
    }
    for rule, pattern in banned_patterns.items():
        if re.search(pattern, source):
            issues.append(ValidationIssue(rule, "Generated TypeScript uses a forbidden capability."))

    allowed_imports = {"@playwright/test"}
    for module_name in re.findall(r"from\s+['\"]([^'\"]+)['\"]", source):
        if module_name not in allowed_imports:
            issues.append(ValidationIssue("disallowed_import", f"Import is not allowed: {module_name}"))

    issues.extend(run_ast_validator(source))
    return dedupe_issues(issues)


def run_ast_validator(source: str) -> list[ValidationIssue]:
    if shutil.which("node") is None:
        return []
    if not _VALIDATOR_SCRIPT.exists():
        return []
    if not (_VALIDATOR_DIR / "node_modules" / "@typescript-eslint" / "typescript-estree").exists():
        return []

    try:
        proc = subprocess.run(
            ["node", str(_VALIDATOR_SCRIPT)],
            input=source,
            capture_output=True,
            text=True,
            timeout=_VALIDATOR_TIMEOUT_SECONDS,
            cwd=_VALIDATOR_DIR,
            check=False,
        )
    except Exception:
        return []

    raw = (proc.stdout or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    return [
        ValidationIssue(
            rule=str(item.get("rule", "unknown")),
            detail=str(item.get("detail") or item.get("snippet") or ""),
        )
        for item in data.get("violations", [])
        if isinstance(item, dict)
    ]


def dedupe_issues(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    seen: set[tuple[str, str]] = set()
    deduped: list[ValidationIssue] = []
    for issue in issues:
        key = (issue.rule, issue.detail)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped
