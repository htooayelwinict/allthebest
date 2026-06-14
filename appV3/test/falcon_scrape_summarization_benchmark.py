#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "mlx-lm>=0.31.0",
# ]
# ///
"""Benchmark Falcon H1 Tiny on noisy web-scrape summarization.

This is a model test harness, not a deterministic summarizer. It builds noisy
HTML/text fixtures with known facts, runs the model through prompt/budget
variants, and records whether the model summary itself is usable.

Run from the repository root:

    uv run --script appV3/test/falcon_scrape_summarization_benchmark.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from refine_scraped_content import (  # noqa: E402
    LinkRecord,
    PROMPT_STYLES,
    build_summary_prompt,
    generate_model_summary,
    model_failure_reason,
    parse_scrape,
)


MODEL_ID = "mlx-community/Falcon-H1-Tiny-R-90M-bf16"
DEFAULT_MODEL_SUITE = [
    "mlx-community/Falcon-H1-Tiny-R-90M-bf16",
    "mlx-community/Falcon-H1-Tiny-90M-Instruct-bf16",
    "mlx-community/Falcon-H1-Tiny-Multilingual-100M-Instruct-bf16",
    "mlx-community/Falcon-H1-Tiny-90M-Instruct-Curriculum-bf16",
]
DEFAULT_OUT = Path("appV3/test/falcon_scrape_summary_benchmark.json")


@dataclass(frozen=True)
class Fixture:
    name: str
    html: str
    expected_terms: list[str]
    forbidden_terms: list[str]


@dataclass(frozen=True)
class BenchmarkCase:
    model: str
    fixture: str
    prompt_style: str
    raw_prompt: bool
    max_input_chars: int
    max_links: int
    max_tokens: int


FIXTURES = [
    Fixture(
        name="cleartrail_product",
        expected_terms=["ClearTrail", "offline", "maps", "battery", "six hours"],
        forbidden_terms=["cookie", "newsletter", "advertisement", "login"],
        html="""
<!doctype html><html><head><title>ClearTrail 2.0 update</title>
<style>.ad{display:none}</style><script>window.ads = true;</script></head>
<body>
<nav>Home | Login | Newsletter | Subscribe</nav>
<aside>ADVERTISEMENT: Boots sale. Cookie settings. Accept all cookies.</aside>
<main>
<h1>ClearTrail 2.0 adds offline hiking maps and battery saver mode</h1>
<p>ClearTrail 2.0 now lets hikers download regional trail packs before trips.</p>
<p>The update switches to low-power GPS tracking when cell service disappears.</p>
<p>The company says battery saver mode can extend phone life by up to six hours during long routes.</p>
<a href="https://example.test/cleartrail-2">Release notes</a>
</main>
<footer>Privacy Terms Cookie Policy</footer>
</body></html>
""",
    ),
    Fixture(
        name="quickcart_sources",
        expected_terms=["QuickCart", "downtown", "15 minutes", "unpredictable", "30 minutes"],
        forbidden_terms=["cookie", "advertisement", "footer"],
        html="""
<html><head><title>QuickCart pickup pilot</title></head>
<body>
<header>Menu Search Sign in</header>
<section>
<h1>QuickCart curbside pickup pilot shows mixed results</h1>
<p>Source A: QuickCart said its downtown store reduced average pickup wait time from 22 minutes to 15 minutes.</p>
<p>Managers said staff training was the main bottleneck for the pilot.</p>
<p>Source B: Drivers on a forum agreed the downtown store is faster, but said pickup windows are still unpredictable.</p>
<p>Several drivers said the suburban store can still take 30 minutes.</p>
<a href="https://example.test/quickcart-pilot">Pilot update</a>
<a href="https://example.test/driver-forum">Driver forum</a>
</section>
<aside>ADVERTISEMENT footer cookie cookie cookie</aside>
</body></html>
""",
    ),
    Fixture(
        name="civic_energy_article",
        expected_terms=["Riverton", "microgrid", "school", "library", "storm"],
        forbidden_terms=["cookie", "subscribe", "advertisement"],
        html="""
<html><head><title>Riverton microgrid plan</title><script>const payload = {"noise": true}</script></head>
<body>
<nav>Home Politics Sports Subscribe Sign in</nav>
<article>
<h1>Riverton approves neighborhood microgrid after winter outages</h1>
<p>Riverton council approved a $12 million neighborhood microgrid after a winter storm left the east side without power for four days.</p>
<p>The first phase connects a school, public library, and emergency shelter to battery storage and rooftop solar.</p>
<p>Officials said construction begins in September and should finish before next winter.</p>
<a href="https://example.test/riverton-microgrid">Council briefing</a>
</article>
<div>ADVERTISEMENT Newsletter Cookie Preferences Related links Footer</div>
</body></html>
""",
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Falcon scrape-summarization benchmark cases.")
    parser.add_argument("--model", default=MODEL_ID)
    parser.add_argument(
        "--models",
        help=(
            "Comma-separated models to compare. Use 'suite' for the default Falcon H1 Tiny MLX suite "
            "found on Hugging Face."
        ),
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--fixtures", help="Comma-separated fixture names to run. Defaults to all fixtures.")
    parser.add_argument("--max-tokens", default="96,160,256", help="Comma-separated generation token budgets.")
    parser.add_argument("--max-input-chars", default="300,600,1000", help="Comma-separated cleaned text budgets.")
    parser.add_argument("--max-links", default="0,2", help="Comma-separated link counts.")
    parser.add_argument(
        "--prompt-style",
        default="completion,instruction",
        help="Comma-separated prompt styles: completion,instruction,sentence,extractive.",
    )
    parser.add_argument("--raw-prompt", default="true,false", help="Comma-separated booleans.")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--limit", type=int, help="Optional max number of cases to run.")
    return parser.parse_args()


def parse_int_list(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def parse_str_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_bool_list(value: str) -> list[bool]:
    mapping = {"true": True, "1": True, "yes": True, "false": False, "0": False, "no": False}
    parsed: list[bool] = []
    for part in parse_str_list(value):
        lowered = part.lower()
        if lowered not in mapping:
            raise SystemExit(f"Invalid boolean value: {part!r}")
        parsed.append(mapping[lowered])
    return parsed


def parse_model_list(args: argparse.Namespace) -> list[str]:
    value = args.models or args.model
    models = DEFAULT_MODEL_SUITE if value.strip().lower() == "suite" else parse_str_list(value)
    deduped: list[str] = []
    for model in models:
        if model and model not in deduped:
            deduped.append(model)
    return deduped


def parse_fixture_list(value: str | None) -> list[Fixture]:
    if not value:
        return FIXTURES
    wanted = set(parse_str_list(value))
    fixtures = [fixture for fixture in FIXTURES if fixture.name in wanted]
    missing = wanted.difference({fixture.name for fixture in fixtures})
    if missing:
        raise SystemExit(f"Unknown fixture name(s): {', '.join(sorted(missing))}")
    return fixtures


def build_cases(args: argparse.Namespace) -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    prompt_styles = parse_str_list(args.prompt_style)
    unknown_prompt_styles = sorted(set(prompt_styles).difference(PROMPT_STYLES))
    if unknown_prompt_styles:
        raise SystemExit(f"Unknown prompt style(s): {', '.join(unknown_prompt_styles)}")
    for fixture in parse_fixture_list(args.fixtures):
        for model in parse_model_list(args):
            for prompt_style in prompt_styles:
                for raw_prompt in parse_bool_list(args.raw_prompt):
                    for max_input_chars in parse_int_list(args.max_input_chars):
                        for max_links in parse_int_list(args.max_links):
                            for max_tokens in parse_int_list(args.max_tokens):
                                cases.append(
                                    BenchmarkCase(
                                        model=model,
                                        fixture=fixture.name,
                                        prompt_style=prompt_style,
                                        raw_prompt=raw_prompt,
                                        max_input_chars=max_input_chars,
                                        max_links=max_links,
                                        max_tokens=max_tokens,
                                    )
                                )
    return cases[: args.limit] if args.limit else cases


def evaluate_output(summary: str, raw_output: str, fixture: Fixture, failure_reason: str | None) -> dict[str, Any]:
    normalized = summary.lower()
    raw_normalized = raw_output.lower()
    expected_hits = [term for term in fixture.expected_terms if term.lower() in normalized]
    raw_expected_hits = [term for term in fixture.expected_terms if term.lower() in raw_normalized]
    forbidden_hits = [term for term in fixture.forbidden_terms if term.lower() in normalized]
    checks = {
        "usable_by_harness": failure_reason is None,
        "has_summary_text": bool(summary.strip()),
        "mentions_at_least_three_expected_terms": len(expected_hits) >= 3,
        "omits_forbidden_terms": not forbidden_hits,
        "raw_mentions_expected_even_if_summary_failed": len(raw_expected_hits) >= 3,
    }
    return {
        "checks": checks,
        "passed": all(checks[key] for key in ("usable_by_harness", "has_summary_text", "mentions_at_least_three_expected_terms", "omits_forbidden_terms")),
        "expected_hits": expected_hits,
        "raw_expected_hits": raw_expected_hits,
        "forbidden_hits": forbidden_hits,
    }


def run_case(case: BenchmarkCase, fixture: Fixture, args: argparse.Namespace, tmp_dir: Path) -> dict[str, Any]:
    input_path = tmp_dir / f"{fixture.name}.html"
    input_path.write_text(fixture.html, encoding="utf-8")

    title, cleaned_text, links, parse_stats = parse_scrape(
        input_path=input_path,
        include_assets=False,
        max_input_chars=case.max_input_chars,
        max_links=case.max_links,
    )

    prompt = build_summary_prompt(cleaned_text, links, case.prompt_style)
    model_args = argparse.Namespace(
        model=case.model,
        temperature=args.temperature,
        top_p=args.top_p,
        max_tokens=case.max_tokens,
        raw_prompt=case.raw_prompt,
        raw_model_output=False,
    )
    summary, raw_output, model_stats = generate_model_summary(prompt, model_args)
    failure_reason = model_failure_reason(summary or "", raw_output or "")
    evaluation = evaluate_output(summary or "", raw_output or "", fixture, failure_reason)

    return {
        "case": asdict(case),
        "title": title,
        "cleaned_text": cleaned_text,
        "links": [asdict(link) for link in links],
        "model_summary": summary,
        "raw_model_output": raw_output,
        "model_failure_reason": failure_reason,
        "evaluation": evaluation,
        "stats": {
            **parse_stats,
            **model_stats,
            "model_summary_usable": failure_reason is None,
        },
    }


def main() -> int:
    args = parse_args()
    cases = build_cases(args)
    results: list[dict[str, Any]] = []

    with TemporaryDirectory(prefix="falcon-scrape-bench-") as tmp:
        tmp_dir = Path(tmp)
        fixtures_by_name = {fixture.name: fixture for fixture in FIXTURES}
        for index, case in enumerate(cases, start=1):
            print(
                f"[{index}/{len(cases)}] {case.fixture} model={case.model} style={case.prompt_style} "
                f"raw={case.raw_prompt} chars={case.max_input_chars} links={case.max_links} tokens={case.max_tokens}",
                flush=True,
            )
            results.append(run_case(case, fixtures_by_name[case.fixture], args, tmp_dir))

    report = {
        "models": parse_model_list(args),
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "case_count": len(results),
        "pass_count": sum(1 for result in results if result["evaluation"]["passed"]),
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}")
    print(f"pass_count={report['pass_count']}/{report['case_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
