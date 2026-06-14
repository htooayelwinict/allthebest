#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "openrouter>=0.9.1",
#   "pydantic>=2.0",
#   "prompt-toolkit>=3.0",
#   "rich>=13.0",
# ]
# ///
"""Thin CLI entrypoint for the appV3 web_research agent."""

from __future__ import annotations

from web_research.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
