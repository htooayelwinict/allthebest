#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "mlx-lm>=0.31.0",
# ]
# ///
"""Ready-to-use TXT/HTML scrape summarizer with Falcon H1 Tiny MLX models.

Example:

    atb --secret-name allthebest/dev/runtime --region us-east-1 -- \
      uv run --script appV3/summarize_txt.py \
      --input appV3/test/news.txt

The script removes common web-scrape junk deterministically, extracts links,
then asks the best tested Falcon H1 Tiny instruct models to summarize the
cleaned content. If the primary model fails quality checks, it tries the next
model/prompt combination and records every attempt in JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


PRIMARY_MODEL = "mlx-community/Falcon-H1-Tiny-90M-Instruct-bf16"
FALLBACK_MODEL = "mlx-community/Falcon-H1-Tiny-Multilingual-100M-Instruct-bf16"
DEFAULT_MODELS = [PRIMARY_MODEL, FALLBACK_MODEL]
DEFAULT_PROMPT_STYLES = ["brief", "structured", "extractive"]
PROMPT_STYLES = ("structured", "brief", "sentence", "extractive")
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
    r"^add as preferred",
    r"^ad choices$",
    r"^ad options$",
    r"^advertis(e|ing)",
    r"^arts$",
    r"^business$",
    r"^close$",
    r"^cookie",
    r"^audio$",
    r"^culture$",
    r"^dismiss$",
    r"^earth$",
    r"^football \d{4}$",
    r"^footer$",
    r"^get the .* app$",
    r"^getty images$",
    r"^health$",
    r"^hide or report",
    r"^home$",
    r"^i do(n't| not|n.t) want to see",
    r"^live$",
    r"^log ?in$",
    r"^news$",
    r"^newsletter",
    r"^more$",
    r"^privacy",
    r"^report this ad$",
    r"^save$",
    r"^skip to",
    r"^site search$",
    r"^sport$",
    r"^subscribe",
    r"^submit$",
    r"^terms",
    r"^technology$",
    r"^travel$",
    r"^video$",
    r"^weather$",
    r"^why am i seeing this ad",
    r"^your feedback will help",
)


@dataclass(frozen=True)
class LinkRecord:
    label: str
    url: str
    kind: str


@dataclass(frozen=True)
class ModelAttempt:
    model: str
    prompt_style: str
    summary: str
    raw_model_output: str
    failure_reason: str | None
    stats: dict[str, int | float | str | bool | None]


@dataclass(frozen=True)
class ParsedScrape:
    title: str | None
    cleaned_text: str
    cleaned_text_sent_to_model: str
    links: list[LinkRecord]
    stats: dict[str, int | float | bool | str | None]


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
    parsed = urlsplit(url)
    display_label = label or label_from_url(url)
    normalized = display_label.lower().strip()
    if looks_like_generic_chrome(normalized):
        return True
    if normalized.startswith("add as preferred"):
        return True
    if any(part in normalized for part in ("cookie", "privacy", "subscription", "terms")):
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

    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    path = parsed.path.lower()
    if any(part in path for part in ("/cookies/", "/privacy", "/terms", "/content-index", "/links-and-feeds", "/send/")):
        return True
    if re.fullmatch(r"(?:[a-z]+-)?help-\d+", normalized):
        return True
    if re.fullmatch(r"[a-z]\d{6,}", normalized):
        return True
    if re.fullmatch(r"c[0-9a-z]{8,}", normalized):
        return True
    if not path_parts and normalized == parsed.netloc.lower():
        return True
    if parsed.netloc.lower().startswith("shop."):
        return True
    if "utm_campaign=footer" in parsed.query.lower():
        return True
    if len(normalized.split()) <= 2 and len(path_parts) <= 2 and re.fullmatch(r"[a-z]{2,15}", normalized):
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


def build_summary_prompt(cleaned_text: str, prompt_style: str) -> str:
    if prompt_style == "structured":
        return (
            "Cleaned web page text:\n"
            f"{cleaned_text}\n\n"
            "Write a useful refined summary for a user. Ignore navigation, ads, cookie banners, captions, footer text, "
            "and markup. Do not copy only the title. Use only facts from the text.\n\n"
            "Return Markdown in this exact shape:\n"
            "Summary:\n"
            "A clear 2-4 sentence overview of the useful content.\n\n"
            "Key points:\n"
            "- 4 to 6 concise bullets with concrete names, numbers, dates, claims, or context from the text.\n"
            "- Do not include links here.\n"
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
        "Cleaned web page text:\n"
        f"{cleaned_text}\n\n"
        "Write a useful user-facing summary in 4 short sentences. Include important people, organizations, "
        "numbers, dates, claims, and context when present. Do not use headings or bullet lists. "
        "Do not copy only the title. Ignore navigation, ads, cookie banners, captions, and markup. "
        "Use only facts from the text.\n\n"
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
    summary = strip_reasoning_markup(raw_output)
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
        "cleaned web page text:",
        "useful cleaned web text:",
        "ignore navigation, ads, cookie banners",
        "write a useful user-facing summary",
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
    title_key = normalize_for_similarity(title or "")
    candidates: list[str] = []
    for line in cleaned_text.splitlines():
        cleaned = normalize_space(line)
        if not cleaned:
            continue
        if title_key and normalize_for_similarity(cleaned) == title_key:
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
    if re.fullmatch(r"#?\d[\d,.\-#]*", normalized):
        return True
    if len(line) < 12:
        return True
    return False


def first_nonempty_lines(cleaned_text: str, limit: int) -> list[str]:
    return [line for line in cleaned_text.splitlines() if normalize_space(line)][:limit]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize a noisy scraped .txt/.html file into Markdown and JSON.")
    parser.add_argument("--input", type=Path, required=True, help="Input .txt/.html file with scraped web content.")
    parser.add_argument("--markdown-out", type=Path, help="Defaults to <input>_summary.md.")
    parser.add_argument("--json-out", type=Path, help="Defaults to <input>_summary.json.")
    parser.add_argument(
        "--models",
        default="auto",
        help="Comma-separated MLX models. Use 'auto' for the tested best models.",
    )
    parser.add_argument(
        "--prompt-styles",
        default=",".join(DEFAULT_PROMPT_STYLES),
        help="Comma-separated prompt styles to try. Defaults to brief,structured,extractive.",
    )
    parser.add_argument(
        "--max-input-chars",
        type=int,
        default=1800,
        help="Cleaned text budget sent to the tiny model. Keep compact for better summaries.",
    )
    parser.add_argument("--max-tokens", type=int, default=240, help="Generation budget for each model attempt.")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--max-links", type=int, default=25, help="Maximum extracted links to write.")
    parser.add_argument("--include-assets", action="store_true", help="Keep asset URLs such as images/scripts/fonts.")
    parser.add_argument("--raw-prompt", action="store_true", help="Bypass chat template. Off by default; benchmark favored template mode.")
    parser.add_argument("--try-all", action="store_true", help="Run every model/prompt attempt and choose the best usable one.")
    parser.add_argument(
        "--strict-model",
        action="store_true",
        help="Exit non-zero instead of using deterministic fallback if all model attempts fail.",
    )
    parser.add_argument(
        "--include-full-cleaned-text",
        action="store_true",
        help="Include the full cleaned text in JSON. Markdown always includes the compact model input.",
    )
    return parser.parse_args()


def parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def resolve_models(value: str) -> list[str]:
    if value.strip().lower() == "auto":
        return DEFAULT_MODELS
    models = parse_csv(value)
    if not models:
        raise SystemExit("--models must include at least one model or 'auto'.")
    return dedupe(models)


def resolve_prompt_styles(value: str) -> list[str]:
    styles = parse_csv(value)
    unknown = sorted(set(styles).difference(PROMPT_STYLES))
    if unknown:
        raise SystemExit(f"Unknown prompt style(s): {', '.join(unknown)}")
    if not styles:
        raise SystemExit("--prompt-styles must include at least one style.")
    return dedupe(styles)


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def default_output_path(input_path: Path, suffix: str) -> Path:
    return input_path.with_name(f"{input_path.stem}_summary{suffix}")


def parse_scraped_file(input_path: Path, include_assets: bool, max_input_chars: int, max_links: int) -> ParsedScrape:
    raw = input_path.read_text(encoding="utf-8", errors="ignore")
    parser = GenericScrapeParser()
    parser.feed(raw)

    parser_text = "\n".join(parser.text_parts)
    if len(split_and_filter_text(parser_text)) < 3:
        parser_text = decode_web_text(re.sub(r"<[^>]+>", "\n", raw))

    cleaned_lines = split_and_filter_text(parser_text)
    cleaned_text = "\n".join(cleaned_lines)
    cleaned_text_sent_to_model = compact_for_model(cleaned_lines, max_chars=max_input_chars)
    links = extract_links(raw=raw, parser_links=parser.links, include_assets=include_assets, max_links=max_links)
    stats: dict[str, int | float | bool | str | None] = {
        "raw_chars": len(raw),
        "cleaned_line_count": len(cleaned_lines),
        "cleaned_chars": len(cleaned_text),
        "cleaned_chars_sent_to_model": len(cleaned_text_sent_to_model),
        "link_count": len(links),
        "input_looks_like_html": "<html" in raw[:2000].lower() or bool(re.search(r"<(body|div|main|article|script)\b", raw[:2000], re.I)),
    }
    return ParsedScrape(
        title=parser.title,
        cleaned_text=cleaned_text,
        cleaned_text_sent_to_model=cleaned_text_sent_to_model,
        links=links,
        stats=stats,
    )


def run_attempt(model: str, prompt_style: str, parsed: ParsedScrape, args: argparse.Namespace) -> ModelAttempt:
    prompt = build_summary_prompt(parsed.cleaned_text_sent_to_model, prompt_style)
    model_args = argparse.Namespace(
        model=model,
        temperature=args.temperature,
        top_p=args.top_p,
        max_tokens=args.max_tokens,
        raw_prompt=args.raw_prompt,
        raw_model_output=False,
    )
    summary, raw_output, stats = generate_model_summary(prompt, model_args)
    polished = polish_summary(summary)
    failure_reason = production_failure_reason(polished, raw_output or "", parsed)
    if failure_reason is None and stats.get("finish_reason") == "length":
        failure_reason = "model_output_hit_token_limit"
    stats.update(
        {
            "prompt_style": prompt_style,
            "raw_prompt": args.raw_prompt,
            "max_tokens": args.max_tokens,
            "model_summary_usable": failure_reason is None,
        }
    )
    return ModelAttempt(
        model=model,
        prompt_style=prompt_style,
        summary=polished,
        raw_model_output=raw_output or "",
        failure_reason=failure_reason,
        stats=stats,
    )


def polish_summary(summary: str) -> str:
    text = normalize_space(summary)
    text = re.sub(r"^\s*summary\s*:\s*", "", text, flags=re.I).strip()
    text = re.split(r"\s+(?:Question|Q)\s*:", text, maxsplit=1, flags=re.I)[0].strip()
    text = re.sub(r"\s+Answer\s*:\s*$", "", text, flags=re.I).strip()
    return text


def production_failure_reason(summary: str, raw_output: str, parsed: ParsedScrape) -> str | None:
    failure_reason = model_failure_reason(summary, raw_output)
    if failure_reason:
        return failure_reason
    if looks_like_title_echo(summary, parsed.title):
        return "model_summary_copied_title_only"
    input_word_count = len(re.findall(r"\w+", parsed.cleaned_text_sent_to_model))
    summary_word_count = len(re.findall(r"\w+", summary))
    if input_word_count >= 90 and summary_word_count < 28:
        return "model_summary_too_short_for_input"
    return None


def looks_like_title_echo(summary: str, title: str | None) -> bool:
    if not title:
        return False
    summary_key = normalize_for_similarity(summary)
    title_key = normalize_for_similarity(title)
    if not summary_key or not title_key:
        return False
    if summary_key == title_key:
        return True
    summary_words = set(summary_key.split())
    title_words = set(title_key.split())
    if not summary_words:
        return False
    overlap = len(summary_words.intersection(title_words)) / len(summary_words)
    return overlap >= 0.85 and len(summary_words) <= len(title_words) + 3


def normalize_for_similarity(value: str) -> str:
    value = value.strip().strip('"').strip("'")
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def choose_attempt(attempts: list[ModelAttempt]) -> ModelAttempt | None:
    usable = [attempt for attempt in attempts if attempt.failure_reason is None]
    if not usable:
        return None
    return max(usable, key=attempt_score)


def attempt_score(attempt: ModelAttempt) -> tuple[int, int, int]:
    words = re.findall(r"\w+", attempt.summary.lower())
    unique_words = len(set(words))
    stopped = 1 if attempt.stats.get("finish_reason") == "stop" else 0
    return (stopped, unique_words, len(attempt.summary))


def summarize(parsed: ParsedScrape, args: argparse.Namespace) -> tuple[str, str, ModelAttempt | None, list[ModelAttempt]]:
    attempts: list[ModelAttempt] = []
    for model in resolve_models(args.models):
        for prompt_style in resolve_prompt_styles(args.prompt_styles):
            print(f"Trying model={model} prompt_style={prompt_style}", flush=True)
            attempt = run_attempt(model=model, prompt_style=prompt_style, parsed=parsed, args=args)
            attempts.append(attempt)
            if attempt.failure_reason is None and not args.try_all:
                return attempt.summary, "model", attempt, attempts

    selected_attempt = choose_attempt(attempts)
    if selected_attempt is not None:
        return selected_attempt.summary, "model", selected_attempt, attempts

    fallback = deterministic_summary(title=parsed.title, cleaned_text=parsed.cleaned_text_sent_to_model, links=parsed.links)
    return fallback, "deterministic_fallback_after_model_failures", None, attempts


def build_payload(parsed: ParsedScrape, args: argparse.Namespace, summary: str, summary_source: str, selected_attempt: ModelAttempt | None, attempts: list[ModelAttempt]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": str(args.input),
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "title": parsed.title,
        "summary": summary,
        "summary_source": summary_source,
        "selected_model": selected_attempt.model if selected_attempt else None,
        "selected_prompt_style": selected_attempt.prompt_style if selected_attempt else None,
        "selected_model_failure_reason": selected_attempt.failure_reason if selected_attempt else None,
        "parameters": {
            "models": resolve_models(args.models),
            "prompt_styles": resolve_prompt_styles(args.prompt_styles),
            "max_input_chars": args.max_input_chars,
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
            "top_p": args.top_p,
            "raw_prompt": args.raw_prompt,
            "max_links": args.max_links,
        },
        "stats": parsed.stats,
        "links": [asdict(link) for link in parsed.links],
        "attempts": [asdict(attempt) for attempt in attempts],
        "cleaned_text_sent_to_model": parsed.cleaned_text_sent_to_model,
    }
    if args.include_full_cleaned_text:
        payload["cleaned_text"] = parsed.cleaned_text
    return payload


def write_markdown(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Refined TXT Summary",
        "",
        f"Source: `{payload['source']}`",
        f"Generated: {payload['generated_at_utc']}",
        f"Summary source: `{payload['summary_source']}`",
        "",
    ]
    if payload.get("title"):
        lines.extend(["## Detected Title", "", str(payload["title"]), ""])

    lines.extend(["## Summary", "", str(payload["summary"]), ""])

    lines.extend(["## Extracted Links", ""])
    links = payload["links"]
    if links:
        for link in links:
            lines.append(f"- [{link['label']}]({link['url']}) - {link['kind']}")
    else:
        lines.append("- No links extracted.")

    lines.extend(["", "## Model Attempts", ""])
    for attempt in payload["attempts"]:
        usable = attempt["failure_reason"] is None
        finish_reason = attempt["stats"].get("finish_reason")
        generation_tokens = attempt["stats"].get("generation_tokens")
        lines.append(
            f"- `{attempt['model']}` / `{attempt['prompt_style']}`: "
            f"usable={usable}, failure={attempt['failure_reason']}, "
            f"finish={finish_reason}, tokens={generation_tokens}"
        )

    lines.extend(["", "## Cleaned Text Sent To Model", "", "```text", str(payload["cleaned_text_sent_to_model"]), "```"])
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_json(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.markdown_out = args.markdown_out or default_output_path(args.input, ".md")
    args.json_out = args.json_out or default_output_path(args.input, ".json")

    parsed = parse_scraped_file(
        input_path=args.input,
        include_assets=args.include_assets,
        max_input_chars=args.max_input_chars,
        max_links=args.max_links,
    )
    summary, summary_source, selected_attempt, attempts = summarize(parsed, args)
    if args.strict_model and summary_source != "model":
        print("No usable model summary was produced. See JSON attempts for diagnostics.", file=sys.stderr)
        return 2

    payload = build_payload(parsed, args, summary, summary_source, selected_attempt, attempts)
    write_markdown(payload, args.markdown_out)
    write_json(payload, args.json_out)

    print(f"Wrote Markdown: {args.markdown_out}")
    print(f"Wrote JSON: {args.json_out}")
    print(f"Summary source: {summary_source}")
    if selected_attempt:
        print(f"Selected model: {selected_attempt.model}")
        print(f"Selected prompt style: {selected_attempt.prompt_style}")
    print(f"Extracted links: {len(parsed.links)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
