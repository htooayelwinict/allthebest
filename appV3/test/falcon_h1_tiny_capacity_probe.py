#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "mlx-lm>=0.31.0",
# ]
# ///
"""Probe Falcon H1 Tiny R 90M across a small capability prompt set.

Run from the repository root:

    uv run --script appV3/test/falcon_h1_tiny_capacity_probe.py

The script uses MLX, so it is intended for Apple silicon Macs. The first run
will download the model from Hugging Face:

    mlx-community/Falcon-H1-Tiny-R-90M-bf16
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


MODEL_ID = "mlx-community/Falcon-H1-Tiny-R-90M-bf16"


@dataclass(frozen=True)
class PromptCase:
    name: str
    capacity: str
    prompt: str


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool


@dataclass(frozen=True)
class ProbeResult:
    name: str
    capacity: str
    prompt: str
    output: str
    elapsed_seconds: float
    prompt_tokens: int | None
    generation_tokens: int | None
    prompt_tps: float | None
    generation_tps: float | None
    peak_memory_gb: float | None
    finish_reason: str | None
    checks: list[CheckResult]


DEFAULT_PROMPT_CASES = [
    PromptCase(
        name="short_qa",
        capacity="factual recall and concise answering",
        prompt="Answer in one short sentence: What is the capital of France?",
    ),
    PromptCase(
        name="arithmetic",
        capacity="single-step arithmetic",
        prompt=(
            "A notebook costs 4 dollars. Mia buys 3 notebooks and pays with "
            "20 dollars. How much change should she receive? Show the answer only."
        ),
    ),
    PromptCase(
        name="instruction_following",
        capacity="format control",
        prompt=(
            "Return exactly three bullet points about keeping a Python script "
            "maintainable. Each bullet must start with '- '."
        ),
    ),
    PromptCase(
        name="json_shape",
        capacity="structured output",
        prompt=(
            "Return only valid JSON with these keys: name, score, reason. "
            "Use name='falcon_probe', score=7, and a short reason."
        ),
    ),
    PromptCase(
        name="summarization",
        capacity="compression and salience",
        prompt=(
            "Summarize this in one sentence: The app receives a vague user "
            "request, decomposes it into a validated envelope, plans bounded "
            "work, executes with a worker kernel, and reports verification."
        ),
    ),
    PromptCase(
        name="code_generation",
        capacity="small code synthesis",
        prompt=(
            "Write a Python function named add_tax that accepts amount and "
            "tax_rate, then returns the amount including tax. Include only code."
        ),
    ),
    PromptCase(
        name="long_context_recall",
        capacity="recall from noisy context",
        prompt=(
            "Remember this project code: AURORA-73. Ignore these distractors: "
            "BETA-11, CITRUS-44, DELTA-02. What is the project code?"
        ),
    ),
    PromptCase(
        name="translation",
        capacity="simple multilingual translation",
        prompt="Translate to Spanish: Good morning, friend.",
    ),
    PromptCase(
        name="web_scrape_noise_filter",
        capacity="scraped-page extraction and boilerplate filtering",
        prompt=(
            "You are given raw scraped webpage text. Extract only the main "
            "article facts as exactly four bullets: title, date, price, and "
            "call to action. Ignore nav, cookie, footer, ads, and related links.\n\n"
            "RAW SCRAPE:\n"
            "Home | Reviews | Subscribe | Cookie settings | Accept all cookies\n"
            "ADVERTISEMENT: Save 40% on garden lights\n"
            "ARTICLE TITLE: SolarLeaf Home Battery launches for renters\n"
            "By Priya Nair | Published March 18, 2026\n"
            "SolarLeaf announced a compact balcony battery for apartment renters. "
            "The unit stores solar energy from clip-on panels and starts at $3,499. "
            "Pre-orders open April 2 in California, Oregon, and Washington.\n"
            "Related: Best window planters | Footer: Contact Privacy Careers"
        ),
    ),
    PromptCase(
        name="web_content_synthesis",
        capacity="multi-source scraped-content synthesis",
        prompt=(
            "Synthesize these scraped source snippets into three bullets. Include "
            "one shared conclusion and one disagreement. Mention Source A and "
            "Source B where relevant.\n\n"
            "SOURCE A SCRAPE:\n"
            "QuickCart pilot update: The new curbside pickup system reduced average "
            "wait time from 22 minutes to 15 minutes in the downtown store. Managers "
            "said staff training was the main bottleneck.\n\n"
            "SOURCE B SCRAPE:\n"
            "QuickCart driver forum: Several drivers said pickup windows are still "
            "unpredictable. They agreed the downtown store is faster, but said the "
            "suburban store often takes 30 minutes."
        ),
    ),
    PromptCase(
        name="web_content_summarization",
        capacity="scraped-content summarization",
        prompt=(
            "Summarize the useful content from this noisy scrape in one sentence "
            "under 35 words. Ignore cookie text, navigation, and newsletter copy.\n\n"
            "RAW SCRAPE:\n"
            "Skip to content | Newsletter signup | We value your privacy | Accept\n"
            "ClearTrail 2.0 adds offline hiking maps and a battery saver mode. "
            "The update lets hikers download regional trail packs before trips, "
            "then switch to low-power GPS tracking when cell service disappears. "
            "The company says the battery saver can extend phone life by up to "
            "six hours during long routes. Popular links: Gear deals, Login, Ads."
        ),
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Falcon H1 Tiny R 90M through multiple capacity prompts."
    )
    parser.add_argument("--model", default=MODEL_ID, help="MLX/Hugging Face model id or local path.")
    parser.add_argument("--max-tokens", type=int, default=160, help="Maximum tokens per prompt.")
    parser.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature.")
    parser.add_argument("--top-p", type=float, default=1.0, help="Nucleus sampling value.")
    parser.add_argument(
        "--prompt",
        action="append",
        default=[],
        help="Custom prompt to run. May be passed multiple times.",
    )
    parser.add_argument(
        "--include-defaults",
        action="store_true",
        help="Run default prompt cases in addition to any custom --prompt values.",
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=[case.name for case in DEFAULT_PROMPT_CASES],
        help="Run only a named default prompt case. May be passed multiple times.",
    )
    parser.add_argument(
        "--raw-prompt",
        action="store_true",
        help="Send prompts directly without applying a chat template.",
    )
    parser.add_argument(
        "--trust-remote-code",
        action="store_true",
        help="Allow tokenizer remote code if the model requires it.",
    )
    parser.add_argument("--json-out", type=Path, help="Optional path for a JSON report.")
    return parser.parse_args()


def build_cases(args: argparse.Namespace) -> list[PromptCase]:
    default_cases = DEFAULT_PROMPT_CASES
    if args.only:
        wanted = set(args.only)
        default_cases = [case for case in DEFAULT_PROMPT_CASES if case.name in wanted]

    custom_cases = [
        PromptCase(
            name=f"custom_{index}",
            capacity="custom prompt",
            prompt=prompt,
        )
        for index, prompt in enumerate(args.prompt, start=1)
    ]

    if custom_cases and not args.include_defaults and not args.only:
        return custom_cases
    return [*default_cases, *custom_cases]


def load_mlx_components(trust_remote_code: bool) -> tuple[Any, Any, Callable[..., Any], Callable[..., Any]]:
    try:
        from mlx_lm import load, stream_generate
        from mlx_lm.sample_utils import make_sampler
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency 'mlx-lm'. Run this file with:\n"
            "  uv run --script appV3/test/falcon_h1_tiny_capacity_probe.py"
        ) from exc

    return load, stream_generate, make_sampler, {"trust_remote_code": trust_remote_code}


def render_prompt(tokenizer: Any, prompt: str, raw_prompt: bool) -> str:
    if raw_prompt:
        return prompt

    messages = [{"role": "user", "content": prompt}]
    apply_chat_template = getattr(tokenizer, "apply_chat_template", None)
    if callable(apply_chat_template):
        try:
            rendered = apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        except TypeError:
            rendered = apply_chat_template(messages, add_generation_prompt=True)
        except Exception:
            rendered = None

        if isinstance(rendered, str):
            return rendered
        if isinstance(rendered, list) and hasattr(tokenizer, "decode"):
            return tokenizer.decode(rendered)

    return f"User: {prompt}\nAssistant:"


def run_case(
    *,
    case: PromptCase,
    model: Any,
    tokenizer: Any,
    stream_generate: Callable[..., Any],
    sampler: Callable[..., Any],
    max_tokens: int,
    raw_prompt: bool,
) -> ProbeResult:
    prompt = render_prompt(tokenizer, case.prompt, raw_prompt)
    chunks: list[str] = []
    final_response = None
    start_time = time.perf_counter()

    for response in stream_generate(
        model,
        tokenizer,
        prompt=prompt,
        max_tokens=max_tokens,
        sampler=sampler,
    ):
        chunks.append(response.text)
        final_response = response

    elapsed = time.perf_counter() - start_time
    output = "".join(chunks).strip()
    return ProbeResult(
        name=case.name,
        capacity=case.capacity,
        prompt=case.prompt,
        output=output,
        elapsed_seconds=elapsed,
        prompt_tokens=getattr(final_response, "prompt_tokens", None),
        generation_tokens=getattr(final_response, "generation_tokens", None),
        prompt_tps=getattr(final_response, "prompt_tps", None),
        generation_tps=getattr(final_response, "generation_tps", None),
        peak_memory_gb=getattr(final_response, "peak_memory", None),
        finish_reason=getattr(final_response, "finish_reason", None),
        checks=evaluate_output(case, output),
    )


def evaluate_output(case: PromptCase, output: str) -> list[CheckResult]:
    text = output.strip()
    lower = text.lower()

    if case.name == "short_qa":
        return [CheckResult("mentions_paris", "paris" in lower)]
    if case.name == "arithmetic":
        return [CheckResult("mentions_8", "8" in text)]
    if case.name == "instruction_following":
        bullet_lines = [line for line in text.splitlines() if line.strip().startswith("- ")]
        return [
            CheckResult("has_three_bullets", len(bullet_lines) == 3),
            CheckResult("all_bullets_start_with_dash_space", bool(bullet_lines) and len(bullet_lines) == len(text.splitlines())),
        ]
    if case.name == "json_shape":
        parsed = first_json_object(text)
        return [
            CheckResult("valid_json_object", isinstance(parsed, dict)),
            CheckResult("has_required_keys", isinstance(parsed, dict) and {"name", "score", "reason"} <= set(parsed)),
        ]
    if case.name == "summarization":
        sentence_count = sum(text.count(mark) for mark in ".!?")
        return [
            CheckResult("non_empty", bool(text)),
            CheckResult("one_or_two_sentences", 0 < sentence_count <= 2),
        ]
    if case.name == "code_generation":
        return [
            CheckResult("defines_add_tax", "def add_tax" in lower),
            CheckResult("contains_return", "return" in lower),
        ]
    if case.name == "long_context_recall":
        return [CheckResult("mentions_aurora_73", "aurora-73" in lower)]
    if case.name == "translation":
        return [CheckResult("contains_spanish_good_morning", "buenos" in lower or "buen" in lower)]
    if case.name == "web_scrape_noise_filter":
        bullet_lines = [line for line in text.splitlines() if line.strip().startswith("- ")]
        return [
            CheckResult("no_think_tags", "<think>" not in lower and "</think>" not in lower),
            CheckResult("has_four_bullets", len(bullet_lines) == 4),
            CheckResult("extracts_title", "solarleaf home battery" in lower),
            CheckResult("extracts_date", "march 18, 2026" in lower),
            CheckResult("extracts_price", "$3,499" in text),
            CheckResult("extracts_call_to_action", "pre-order" in lower or "preorder" in lower),
            CheckResult("filters_boilerplate", "cookie" not in lower and "advertisement" not in lower and "footer" not in lower),
        ]
    if case.name == "web_content_synthesis":
        bullet_lines = [line for line in text.splitlines() if line.strip().startswith("- ")]
        return [
            CheckResult("no_think_tags", "<think>" not in lower and "</think>" not in lower),
            CheckResult("has_three_bullets", len(bullet_lines) == 3),
            CheckResult("mentions_shared_faster_downtown", "downtown" in lower and "faster" in lower),
            CheckResult("mentions_disagreement_or_unpredictable", "unpredictable" in lower or "disagree" in lower or "30 minutes" in lower),
            CheckResult("mentions_sources", "source a" in lower and "source b" in lower),
        ]
    if case.name == "web_content_summarization":
        word_count = len(text.split())
        sentence_count = sum(text.count(mark) for mark in ".!?")
        return [
            CheckResult("no_think_tags", "<think>" not in lower and "</think>" not in lower),
            CheckResult("mentions_cleartrail", "cleartrail" in lower),
            CheckResult("mentions_offline_maps", "offline" in lower and "map" in lower),
            CheckResult("mentions_battery_saver", "battery" in lower),
            CheckResult("under_35_words", 0 < word_count <= 35),
            CheckResult("one_sentence", sentence_count == 1),
            CheckResult("filters_boilerplate", "cookie" not in lower and "newsletter" not in lower and "login" not in lower),
        ]

    return [CheckResult("non_empty", bool(text))]


def first_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def print_result(result: ProbeResult, index: int, total: int) -> None:
    print(f"[{index}/{total}] {result.name} - {result.capacity}")
    print(f"Prompt: {result.prompt}")
    print("Output:")
    print(indent_block(result.output or "<empty>"))
    print("Checks:")
    for check in result.checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"  [{status}] {check.name}")
    print(
        "Metrics: "
        f"elapsed={result.elapsed_seconds:.2f}s, "
        f"prompt_tokens={result.prompt_tokens}, "
        f"generation_tokens={result.generation_tokens}, "
        f"prompt_tps={format_optional_float(result.prompt_tps)}, "
        f"generation_tps={format_optional_float(result.generation_tps)}, "
        f"peak_memory_gb={format_optional_float(result.peak_memory_gb)}, "
        f"finish_reason={result.finish_reason}"
    )
    print("-" * 80)


def indent_block(text: str) -> str:
    return "\n".join(f"  {line}" for line in text.splitlines())


def format_optional_float(value: float | None) -> str:
    if value is None:
        return "None"
    return f"{value:.2f}"


def main() -> int:
    args = parse_args()
    cases = build_cases(args)
    if not cases:
        print("No prompt cases selected.", file=sys.stderr)
        return 2

    if platform.system() != "Darwin":
        print("Warning: MLX is designed for Apple silicon; this may fail on this platform.", file=sys.stderr)

    load, stream_generate, make_sampler, tokenizer_config = load_mlx_components(args.trust_remote_code)
    print(f"Loading model: {args.model}")
    model, tokenizer = load(args.model, tokenizer_config=tokenizer_config)
    sampler = make_sampler(temp=args.temperature, top_p=args.top_p)

    print(f"Running {len(cases)} prompt case(s) with max_tokens={args.max_tokens}")
    print("-" * 80)

    results = [
        run_case(
            case=case,
            model=model,
            tokenizer=tokenizer,
            stream_generate=stream_generate,
            sampler=sampler,
            max_tokens=args.max_tokens,
            raw_prompt=args.raw_prompt,
        )
        for case in cases
    ]

    for index, result in enumerate(results, start=1):
        print_result(result, index, len(results))

    total_checks = sum(len(result.checks) for result in results)
    passed_checks = sum(check.passed for result in results for check in result.checks)
    print(f"Summary: {passed_checks}/{total_checks} heuristic checks passed.")

    if args.json_out:
        report = {
            "model": args.model,
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
            "top_p": args.top_p,
            "results": [
                {
                    **asdict(result),
                    "checks": [asdict(check) for check in result.checks],
                }
                for result in results
            ],
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote JSON report to {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
