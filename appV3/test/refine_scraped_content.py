#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "mlx-lm>=0.31.0",
# ]
# ///
"""Test model summarization on arbitrary noisy web-scraped text.

Run with the local MLX model:

    uv run --script appV3/test/refine_scraped_content.py \
      --input path/to/scraped_content.txt

The deterministic part is intentionally generic: remove markup/script noise,
compact duplicated text, and extract non-asset links. The summary is model-only
by default so the output reflects whether the model actually handled the noisy
scrape. A deterministic fallback can be written explicitly with
--allow-fallback-summary, but it is not used by default.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


MODEL_ID = "mlx-community/Falcon-H1-Tiny-Multilingual-100M-Instruct-bf16"
PROMPT_STYLES = ("brief", "completion", "instruction", "sentence", "extractive")
_MODEL_CACHE: dict[str, tuple[Any, Any]] = {}
SKIP_TAGS = {"aside", "footer", "form", "header", "nav", "script", "style", "svg", "noscript", "template"}
BLOCK_TAGS = {
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}
ASSET_EXTENSIONS = {
    ".avif",
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".map",
    ".mjs",
    ".png",
    ".svg",
    ".ttf",
    ".webmanifest",
    ".webp",
    ".woff",
    ".woff2",
}
ASSET_HOST_PREFIXES = ("assets.", "cdn.", "images.", "img.", "media.", "static.")
GENERIC_CHROME_PATTERNS = (
    r"^accept( all)?( cookies)?$",
    r"^ad choices$",
    r"^ad options$",
    r"^advertis(e|ing)",
    r"^close$",
    r"^cookie",
    r"^dismiss$",
    r"^footer$",
    r"^get the .* app$",
    r"^hide or report",
    r"^home$",
    r"^i do(n't| not|n.t) want to see",
    r"^log ?in$",
    r"^newsletter",
    r"^more$",
    r"^privacy",
    r"^report this ad$",
    r"^save$",
    r"^skip to",
    r"^site search$",
    r"^subscribe",
    r"^submit$",
    r"^terms",
    r"^why am i seeing this ad",
    r"^your feedback will help",
)


@dataclass(frozen=True)
class LinkRecord:
    label: str
    url: str
    kind: str


@dataclass(frozen=True)
class RefinedScrape:
    source: str
    title: str | None
    summary: str | None
    model_summary: str | None
    deterministic_summary: str
    model_failure_reason: str | None
    cleaned_text: str
    links: list[LinkRecord]
    stats: dict[str, int | float | bool | str | None]
    raw_model_output: str | None


class GenericScrapeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._anchor_stack: list[dict[str, Any]] = []
        self._title_depth = 0
        self._title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []

    @property
    def title(self) -> str | None:
        title = normalize_space(" ".join(self._title_parts))
        return title or None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return

        if tag == "title":
            self._title_depth += 1
        if tag in BLOCK_TAGS:
            self.text_parts.append("\n")
        if tag == "a":
            self._anchor_stack.append(
                {
                    "href": attrs_dict.get("href", ""),
                    "label": attrs_dict.get("aria-label", "") or attrs_dict.get("title", ""),
                    "text": [],
                }
            )

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return

        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag == "a" and self._anchor_stack:
            current = self._anchor_stack.pop()
            label = normalize_space(" ".join(current["text"])) or normalize_space(str(current["label"]))
            href = clean_url(str(current["href"]))
            if href:
                self.links.append((label, href))
        if tag in BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = decode_web_text(data)
        if not text:
            return
        if self._title_depth:
            self._title_parts.append(text)
        self.text_parts.append(text)
        if self._anchor_stack:
            self._anchor_stack[-1]["text"].append(text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refine arbitrary scraped web content and summarize it.")
    parser.add_argument("--input", type=Path, required=True, help="Scraped HTML/text file to refine.")
    parser.add_argument("--markdown-out", type=Path, help="Markdown output path. Defaults to <input>_refined.md.")
    parser.add_argument("--json-out", type=Path, help="JSON output path. Defaults to <input>_refined.json.")
    parser.add_argument("--model", default=MODEL_ID, help="MLX/Hugging Face model id or local path.")
    parser.add_argument("--max-tokens", type=int, default=160, help="Maximum tokens for the model summary.")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--max-input-chars", type=int, default=1800, help="Cleaned text budget sent to the model.")
    parser.add_argument("--max-links", type=int, default=40, help="Maximum links to include in outputs and prompt.")
    parser.add_argument("--include-assets", action="store_true", help="Keep image/script/style/font URLs.")
    parser.add_argument("--no-model", action="store_true", help="Skip the model and write the deterministic summary.")
    parser.add_argument(
        "--allow-fallback-summary",
        action="store_true",
        help="Use the deterministic summary as summary when model output is unusable.",
    )
    parser.add_argument(
        "--prompt-style",
        choices=PROMPT_STYLES,
        default="sentence",
        help="Prompt shape to send to the model. sentence/extractive are simpler for tiny models.",
    )
    parser.add_argument("--save-prompt", type=Path, help="Optional path to write the exact prompt sent before chat templating.")
    parser.add_argument(
        "--diagnostic-sweep",
        action="store_true",
        help="Run the model across several input budgets and write all model outcomes.",
    )
    parser.add_argument(
        "--sweep-input-chars",
        default="512,1024,2048,4096,8192",
        help="Comma-separated cleaned-text budgets for --diagnostic-sweep.",
    )
    parser.add_argument("--raw-prompt", action="store_true", help="Send the summarization prompt directly without a chat template.")
    parser.add_argument("--raw-model-output", action="store_true", help="Do not remove <think> blocks from model output.")
    return parser.parse_args()


def default_output_path(input_path: Path, suffix: str) -> Path:
    return input_path.with_name(f"{input_path.stem}_refined{suffix}")


def decode_web_text(value: str) -> str:
    text = html.unescape(value)
    text = text.replace("\\u0026", "&").replace("\\/", "/").replace("\\n", "\n").replace("\\t", " ")

    def replace_unicode_escape(match: re.Match[str]) -> str:
        try:
            return chr(int(match.group(1), 16))
        except ValueError:
            return match.group(0)

    text = re.sub(r"\\u([0-9a-fA-F]{4})", replace_unicode_escape, text)
    return normalize_space(text)


def normalize_space(value: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", value).strip()


def clean_url(value: str) -> str:
    url = html.unescape(value).strip().strip("'\"")
    url = url.replace("\\u0026", "&").replace("\\/", "/").rstrip("\\")
    return url


def line_signal_score(line: str) -> int:
    score = 0
    word_count = len(line.split())
    if len(line) < 4:
        score -= 2
    if word_count <= 2 and not re.search(r"\d", line):
        score -= 1
    if re.fullmatch(r"#?\d[\d,.\-#]*", line):
        score -= 3
    if len(line) >= 30:
        score += 2
    if re.search(r"[.!?။]$", line):
        score += 2
    if re.search(r"\b(announced|because|includes|reported|said|shows|updated|will|can|new|launch|release|study)\b", line, re.I):
        score += 1
    if re.search(r"\d", line):
        score += 1
    if re.search(r"https?://", line):
        score -= 2
    if looks_like_code_or_payload(line):
        score -= 4
    return score


def looks_like_code_or_payload(line: str) -> bool:
    if len(line) > 3000:
        return True
    symbols = sum(1 for char in line if char in "{}[]<>;$=\\/|")
    if len(line) > 80 and symbols / max(len(line), 1) > 0.16:
        return True
    if re.search(r"\b(function|const|var|webpack|nonce|stylesheet|script-src|data-testid)\b", line):
        return True
    return False


def split_and_filter_text(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in re.split(r"\n+", text):
        line = normalize_space(raw_line)
        if not line:
            continue
        if len(line) <= 1:
            continue
        if looks_like_code_or_payload(line):
            continue
        if looks_like_generic_chrome(line):
            continue
        if re.fullmatch(r"[\W_]+", line):
            continue
        lines.append(line)
    return unique_by_normalized(lines)


def looks_like_generic_chrome(line: str) -> bool:
    normalized = line.lower().strip()
    if any(re.search(pattern, normalized) for pattern in GENERIC_CHROME_PATTERNS):
        return True
    if len(normalized.split()) <= 3 and normalized in {"help", "menu", "next", "previous", "search", "share", "subscribe"}:
        return True
    return False


def unique_by_normalized(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        key = re.sub(r"\W+", "", value.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def compact_for_model(lines: list[str], max_chars: int) -> str:
    scores = [line_signal_score(line) for line in lines]
    positive_indexes = {index for index, score in enumerate(scores) if score > 0}
    neighbor_indexes = {
        neighbor
        for index in positive_indexes
        for neighbor in (index - 1, index, index + 1)
        if 0 <= neighbor < len(lines)
    }
    high_signal = [line for index, line in enumerate(lines) if index in neighbor_indexes and scores[index] >= -1]
    selected: list[str] = []
    total = 0
    for line in high_signal or lines:
        added = len(line) + 1
        if total + added > max_chars:
            break
        selected.append(line)
        total += added
    return "\n".join(selected)


def extract_links(raw: str, parser_links: list[tuple[str, str]], include_assets: bool, max_links: int) -> list[LinkRecord]:
    if max_links <= 0:
        return []

    regex_links = [(label_from_url(clean_url(url)), clean_url(url)) for url in re.findall(r"https?://[^\\\"'<> )\]]+", raw)]
    links: list[LinkRecord] = []
    seen: set[str] = set()
    for label, url in [*parser_links, *regex_links]:
        url = clean_url(url)
        if not url.startswith(("http://", "https://")):
            continue
        if not include_assets and is_asset_url(url):
            continue
        if not include_assets and looks_like_low_value_link(label, url):
            continue
        if url in seen:
            continue
        seen.add(url)
        links.append(LinkRecord(label=label or label_from_url(url), url=url, kind=link_kind(url)))
        if len(links) >= max_links:
            break
    return links


def looks_like_low_value_link(label: str, url: str) -> bool:
    normalized = label.lower().strip()
    if looks_like_generic_chrome(normalized):
        return True
    if normalized in {
        "business",
        "home",
        "news",
        "newsletters",
        "save",
        "search",
        "shop",
        "site search",
        "sport",
        "weather",
    }:
        return True

    parsed = urlsplit(url)
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(normalized.split()) <= 2 and len(path_parts) <= 1 and re.fullmatch(r"[a-z]{2,15}", normalized):
        return True
    return False


def is_asset_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if url == "http://www.w3.org/2000/svg":
        return True
    if host.startswith(ASSET_HOST_PREFIXES):
        return True
    if any(path.endswith(ext) for ext in ASSET_EXTENSIONS):
        return True
    return any(part in path for part in ("/assets/", "/static/", "/_next/", "/cdn-cgi/", "/fonts/"))


def link_kind(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path.lower()
    if any(path.endswith(ext) for ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv")):
        return "document"
    if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".mp4", ".mov")):
        return "media"
    if is_asset_url(url):
        return "asset"
    return "page"


def label_from_url(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path.strip("/")
    if not path:
        return parsed.netloc
    return path.split("/")[-1] or parsed.netloc


def parse_scrape(input_path: Path, include_assets: bool, max_input_chars: int, max_links: int) -> tuple[str | None, str, list[LinkRecord], dict[str, int | float | bool | str | None]]:
    raw = input_path.read_text(encoding="utf-8", errors="ignore")
    parser = GenericScrapeParser()
    parser.feed(raw)

    parser_text = "\n".join(parser.text_parts)
    if len(split_and_filter_text(parser_text)) < 3:
        parser_text = decode_web_text(re.sub(r"<[^>]+>", "\n", raw))

    cleaned_lines = split_and_filter_text(parser_text)
    cleaned_text = compact_for_model(cleaned_lines, max_chars=max_input_chars)
    links = extract_links(raw=raw, parser_links=parser.links, include_assets=include_assets, max_links=max_links)
    stats: dict[str, int | float | bool | str | None] = {
        "raw_chars": len(raw),
        "cleaned_line_count": len(cleaned_lines),
        "cleaned_chars_sent_to_model": len(cleaned_text),
        "link_count": len(links),
        "input_looks_like_html": "<html" in raw[:2000].lower() or bool(re.search(r"<(body|div|main|article|script)\b", raw[:2000], re.I)),
    }
    return parser.title, cleaned_text, links, stats


def build_summary_prompt(cleaned_text: str, links: list[LinkRecord], prompt_style: str) -> str:
    if prompt_style == "brief":
        return (
            "Cleaned web page text:\n"
            f"{cleaned_text}\n\n"
            "Write a useful user-facing summary in 2 short sentences. "
            "Do not copy only the title. Ignore navigation, ads, cookie banners, captions, and markup. "
            "Use only facts from the text.\n\n"
            "Summary:"
        )

    if prompt_style == "instruction":
        return (
            "CLEANED TEXT:\n"
            f"{cleaned_text}\n\n"
            "Task: summarize the useful page content for a user. Ignore navigation, ads, cookie text, and markup. "
            "Return only the final answer with a short Summary and three Key points. Do not invent URLs."
        )

    if prompt_style == "sentence":
        return (
            "Text:\n"
            f"{cleaned_text}\n\n"
            "In one plain sentence, this page says:"
        )

    if prompt_style == "extractive":
        return (
            "Useful cleaned web text:\n"
            f"{cleaned_text}\n\n"
            "Short user summary using only those facts:"
        )

    return (
        "Summarize the useful content below. Ignore navigation, ads, cookie text, and markup.\n\n"
        "CLEANED WEB TEXT:\n"
        f"{cleaned_text}\n\n"
        "Final answer:\n"
        "Summary:"
    )


def generate_model_summary(prompt: str, args: argparse.Namespace) -> tuple[str, str, dict[str, int | float | str | bool | None]]:
    try:
        from mlx_lm import load, stream_generate
        from mlx_lm.sample_utils import make_sampler
    except ImportError as exc:
        raise SystemExit("mlx-lm is not installed. Run this file with uv run --script.") from exc

    model, tokenizer = load_cached_model(args.model, load)
    sampler = make_sampler(temp=args.temperature, top_p=args.top_p)

    rendered = prompt if args.raw_prompt else render_prompt(tokenizer, prompt)
    prompt_token_count = count_prompt_tokens(tokenizer, rendered)
    chunks: list[str] = []
    final_response = None
    start_time = time.perf_counter()
    for response in stream_generate(
        model,
        tokenizer,
        prompt=rendered,
        max_tokens=args.max_tokens,
        sampler=sampler,
    ):
        chunks.append(response.text)
        final_response = response

    elapsed = time.perf_counter() - start_time
    raw_output = "".join(chunks).strip()
    summary = raw_output if args.raw_model_output else strip_reasoning_markup(raw_output)
    diagnostics: dict[str, int | float | str | bool | None] = {
        "prompt_chars": len(prompt),
        "rendered_prompt_chars": len(rendered),
        "prompt_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "prompt_tokens_estimate": prompt_token_count,
        "generation_chunk_count": len(chunks),
        "nonempty_generation_chunk_count": sum(1 for chunk in chunks if chunk),
        "raw_model_output_chars": len(raw_output),
        "elapsed_seconds": elapsed,
        "finish_reason": getattr(final_response, "finish_reason", None),
        "generation_tokens": getattr(final_response, "generation_tokens", None),
        "prompt_tps": getattr(final_response, "prompt_tps", None),
        "generation_tps": getattr(final_response, "generation_tps", None),
    }
    return summary, raw_output, diagnostics


def load_cached_model(model_id: str, load_fn: Any) -> tuple[Any, Any]:
    cached = _MODEL_CACHE.get(model_id)
    if cached is not None:
        return cached
    loaded = load_fn(model_id)
    _MODEL_CACHE[model_id] = loaded
    return loaded


def count_prompt_tokens(tokenizer: Any, rendered_prompt: str) -> int | None:
    try:
        encoded = tokenizer.encode(rendered_prompt)
    except Exception:
        return None
    try:
        return len(encoded)
    except TypeError:
        return None


def render_prompt(tokenizer: Any, prompt: str) -> str:
    apply_chat_template = getattr(tokenizer, "apply_chat_template", None)
    if callable(apply_chat_template):
        rendered = apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=False,
            add_generation_prompt=True,
        )
        if isinstance(rendered, str):
            return rendered
    return f"User: {prompt}\nAssistant:"


def strip_reasoning_markup(output: str) -> str:
    if "</think>" in output:
        output = output.rsplit("</think>", 1)[-1]
    output = re.sub(r"<think>.*", "", output, flags=re.I | re.S) if "<think>" in output.lower() else output
    output = re.sub(r"</?think>", "", output, flags=re.I)
    return output.strip()


def model_failure_reason(summary: str, raw_output: str) -> str | None:
    if not summary:
        if raw_output:
            return "model_output_was_reasoning_only_after_stripping"
        return "model_returned_empty_output"
    lowered = summary.lower()
    if looks_like_placeholder_output(summary):
        return "model_output_placeholder_template"
    if looks_like_prompt_echo(summary):
        return "model_output_echoed_prompt"
    if "<think>" in lowered or "the user wants" in lowered:
        return "model_output_contains_reasoning"
    if len(summary.split()) < 12:
        return "model_summary_too_short"
    words = re.findall(r"\w+", lowered)
    if len(words) >= 80:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio < 0.28:
            return "model_output_repetitive"
    if raw_output and raw_output.count("<think>") > raw_output.count("</think>"):
        return "model_reasoning_block_unclosed"
    return None


def looks_like_placeholder_output(output: str) -> bool:
    lowered = output.lower()
    placeholder_patterns = (
        r"\[paragraph\s*\d*\]",
        r"\[keyword\]",
        r"\[key point\]",
        r"\[summary\]",
        r"\[headline\]",
        r"\[address\]",
        r"\[name\]",
        r"\[date\]",
        r"\[\d+\]\s*\.\.\.",
    )
    return any(re.search(pattern, lowered) for pattern in placeholder_patterns)


def looks_like_prompt_echo(output: str) -> bool:
    lowered = output.lower()
    echoed_markers = (
        "cleaned web text:",
        "useful cleaned web text:",
        "summarize the useful content below",
        "ignore navigation, ads, cookie text",
        "return only the final answer",
        "task: summarize",
    )
    return any(marker in lowered for marker in echoed_markers)


def deterministic_summary(title: str | None, cleaned_text: str, links: list[LinkRecord]) -> str:
    candidates = summary_candidates(cleaned_text, title)
    summary_lines = candidates[:2] or first_nonempty_lines(cleaned_text, limit=2)
    key_points = candidates[2:5] or candidates[:3] or first_nonempty_lines(cleaned_text, limit=3)
    useful_links = links[:5]

    sections = ["Summary:"]
    if summary_lines:
        sections.append(" ".join(summary_lines))
    else:
        sections.append("No substantive text was found after cleaning the scrape.")

    sections.extend(["", "Key points:"])
    if key_points:
        sections.extend(f"- {line}" for line in key_points)
    else:
        sections.append("- No key points could be extracted from the cleaned text.")

    sections.extend(["", "Useful links:"])
    if useful_links:
        sections.extend(f"- {link.label}: {link.url}" for link in useful_links)
    else:
        sections.append("- No useful links were extracted.")
    return "\n".join(sections)


def summary_candidates(cleaned_text: str, title: str | None) -> list[str]:
    title_key = normalize_for_dedupe(title or "")
    candidates: list[str] = []
    for line in cleaned_text.splitlines():
        cleaned = normalize_space(line)
        if not cleaned:
            continue
        if title_key and normalize_for_dedupe(cleaned) == title_key:
            continue
        if looks_like_generic_chrome(cleaned):
            continue
        if reject_summary_line(cleaned):
            continue
        if len(cleaned) >= 55 or line_signal_score(cleaned) >= 2:
            candidates.append(cleaned)
    return unique_by_normalized(candidates)


def reject_summary_line(line: str) -> bool:
    normalized = line.lower().strip()
    if normalized.startswith(("skip ", "end of ", "read more", "related", "share this", "follow us")):
        return True
    if normalized in {"bbc news", "view all recommendations"}:
        return True
    if re.fullmatch(r"#?\d[\d,.\-#]*", normalized):
        return True
    if len(line) < 12:
        return True
    return False


def first_nonempty_lines(cleaned_text: str, limit: int) -> list[str]:
    return [line for line in cleaned_text.splitlines() if normalize_space(line)][:limit]


def normalize_for_dedupe(value: str) -> str:
    return re.sub(r"\W+", "", value.lower())


def refine(args: argparse.Namespace) -> RefinedScrape:
    title, cleaned_text, links, stats = parse_scrape(
        input_path=args.input,
        include_assets=args.include_assets,
        max_input_chars=args.max_input_chars,
        max_links=args.max_links,
    )
    fallback_summary = deterministic_summary(title=title, cleaned_text=cleaned_text, links=links)
    summary: str | None = fallback_summary if args.no_model else None
    model_summary: str | None = None
    failure_reason: str | None = None
    raw_model_output: str | None = None
    prompt = build_summary_prompt(cleaned_text, links, args.prompt_style)
    stats["prompt_style"] = args.prompt_style
    stats["raw_prompt"] = args.raw_prompt
    stats["max_input_chars"] = args.max_input_chars
    stats["max_links"] = args.max_links

    if args.save_prompt:
        args.save_prompt.parent.mkdir(parents=True, exist_ok=True)
        args.save_prompt.write_text(prompt, encoding="utf-8")
        stats["saved_prompt"] = str(args.save_prompt)

    if not args.no_model:
        model_summary, raw_model_output, model_diagnostics = generate_model_summary(prompt, args)
        failure_reason = model_failure_reason(model_summary or "", raw_model_output or "")
        stats.update(model_diagnostics)
        stats["model_used"] = True
        stats["model_summary_usable"] = failure_reason is None
        if failure_reason is None:
            summary = model_summary
            stats["summary_source"] = "model"
        elif args.allow_fallback_summary:
            summary = fallback_summary
            stats["summary_source"] = "deterministic_fallback_after_model"
        else:
            summary = model_summary
            stats["summary_source"] = "model_unusable"
    else:
        stats["model_used"] = False
        stats["model_summary_usable"] = False
        stats["summary_source"] = "deterministic_no_model"

    return RefinedScrape(
        source=str(args.input),
        title=title,
        summary=summary,
        model_summary=model_summary,
        deterministic_summary=fallback_summary,
        model_failure_reason=failure_reason,
        cleaned_text=cleaned_text,
        links=links,
        stats=stats,
        raw_model_output=raw_model_output,
    )


def write_markdown(content: RefinedScrape, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Refined Scraped Content",
        "",
        f"Source: `{content.source}`",
        f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "",
    ]
    if content.title:
        lines.extend(["## Detected Title", "", content.title, ""])

    lines.extend(["## Model Summary", ""])
    if content.stats.get("model_used"):
        if content.model_failure_reason:
            lines.append(f"Model output unusable: `{content.model_failure_reason}`.")
        if content.model_summary:
            lines.extend(["", content.model_summary])
        elif not content.model_failure_reason:
            lines.append("The model ran, but no summary text was produced.")
    else:
        lines.append("Model summary was skipped because --no-model was used.")

    lines.extend(["", "## Selected Summary", ""])
    if content.summary:
        lines.append(content.summary)
    elif content.stats.get("model_used"):
        lines.append("No selected summary. The model output was unusable and fallback summary was not enabled.")
    else:
        lines.append(content.deterministic_summary)

    lines.extend(["", "## Deterministic Reference Summary", "", content.deterministic_summary])
    lines.extend(["", "## Model Diagnostics", ""])
    diagnostic_keys = [
        "summary_source",
        "model_summary_usable",
        "prompt_style",
        "raw_prompt",
        "prompt_tokens_estimate",
        "prompt_chars",
        "rendered_prompt_chars",
        "generation_chunk_count",
        "nonempty_generation_chunk_count",
        "raw_model_output_chars",
        "finish_reason",
        "generation_tokens",
        "elapsed_seconds",
        "prompt_sha256",
    ]
    for key in diagnostic_keys:
        if key in content.stats:
            lines.append(f"- {key}: {content.stats[key]}")
    lines.extend(["", "## Extracted Links", ""])
    if content.links:
        lines.extend(f"- [{link.label}]({link.url}) - {link.kind}" for link in content.links)
    else:
        lines.append("- No links extracted.")
    lines.extend(["", "## Cleaned Text Sent To Model", "", "```text", content.cleaned_text, "```"])
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_json(content: RefinedScrape, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **asdict(content),
        "links": [asdict(link) for link in content.links],
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_sweep_budgets(value: str) -> list[int]:
    budgets: list[int] = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        try:
            budget = int(part)
        except ValueError as exc:
            raise SystemExit(f"Invalid --sweep-input-chars value: {part!r}") from exc
        if budget <= 0:
            raise SystemExit("--sweep-input-chars values must be positive integers.")
        budgets.append(budget)
    if not budgets:
        raise SystemExit("--sweep-input-chars must include at least one budget.")
    return budgets


def run_diagnostic_sweep(args: argparse.Namespace) -> dict[str, Any]:
    if args.no_model:
        raise SystemExit("--diagnostic-sweep requires model execution; remove --no-model.")

    results: list[dict[str, Any]] = []
    for budget in parse_sweep_budgets(args.sweep_input_chars):
        title, cleaned_text, links, stats = parse_scrape(
            input_path=args.input,
            include_assets=args.include_assets,
            max_input_chars=budget,
            max_links=args.max_links,
        )
        prompt = build_summary_prompt(cleaned_text, links, args.prompt_style)
        model_summary, raw_model_output, model_diagnostics = generate_model_summary(prompt, args)
        failure_reason = model_failure_reason(model_summary or "", raw_model_output or "")
        stats.update(model_diagnostics)
        stats["model_used"] = True
        stats["model_summary_usable"] = failure_reason is None
        stats["summary_source"] = "model" if failure_reason is None else "model_unusable"
        stats["max_input_chars"] = budget
        stats["max_links"] = args.max_links
        stats["prompt_style"] = args.prompt_style
        stats["raw_prompt"] = args.raw_prompt

        results.append(
            {
                "max_input_chars": budget,
                "title": title,
                "model_summary": model_summary,
                "model_failure_reason": failure_reason,
                "raw_model_output": raw_model_output,
                "cleaned_text": cleaned_text,
                "links": [asdict(link) for link in links],
                "stats": stats,
            }
        )

    return {
        "source": str(args.input),
        "model": args.model,
        "prompt_style": args.prompt_style,
        "raw_prompt": args.raw_prompt,
        "max_tokens": args.max_tokens,
        "sweep_input_chars": parse_sweep_budgets(args.sweep_input_chars),
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": results,
    }


def write_sweep_json(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_sweep_markdown(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Scraped Content Model Diagnostic Sweep",
        "",
        f"Source: `{report['source']}`",
        f"Model: `{report['model']}`",
        f"Generated: {report['generated_at_utc']}",
        "",
        "## Results",
        "",
    ]
    for result in report["results"]:
        stats = result["stats"]
        lines.extend(
            [
                f"### max_input_chars={result['max_input_chars']}",
                "",
                f"- usable: {stats['model_summary_usable']}",
                f"- failure_reason: {result['model_failure_reason']}",
                f"- prompt_tokens_estimate: {stats.get('prompt_tokens_estimate')}",
                f"- generation_chunk_count: {stats.get('generation_chunk_count')}",
                f"- nonempty_generation_chunk_count: {stats.get('nonempty_generation_chunk_count')}",
                f"- raw_model_output_chars: {stats.get('raw_model_output_chars')}",
                f"- finish_reason: {stats.get('finish_reason')}",
                "",
                "Model summary:",
                "",
                result["model_summary"] or "<empty>",
                "",
            ]
        )
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if args.diagnostic_sweep:
        args.markdown_out = args.markdown_out or args.input.with_name(f"{args.input.stem}_sweep.md")
        args.json_out = args.json_out or args.input.with_name(f"{args.input.stem}_sweep.json")
        report = run_diagnostic_sweep(args)
        write_sweep_markdown(report, args.markdown_out)
        write_sweep_json(report, args.json_out)
        print(f"Wrote Markdown: {args.markdown_out}")
        print(f"Wrote JSON: {args.json_out}")
        print(f"Sweep runs: {len(report['results'])}")
        return 0

    args.markdown_out = args.markdown_out or default_output_path(args.input, ".md")
    args.json_out = args.json_out or default_output_path(args.input, ".json")
    content = refine(args)
    write_markdown(content, args.markdown_out)
    write_json(content, args.json_out)

    print(f"Wrote Markdown: {args.markdown_out}")
    print(f"Wrote JSON: {args.json_out}")
    print(f"Cleaned chars sent to model: {content.stats['cleaned_chars_sent_to_model']}")
    print(f"Extracted links: {len(content.links)}")
    print(f"Model used: {content.stats['model_used']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
