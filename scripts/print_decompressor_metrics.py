#!/usr/bin/env python3
"""Print decompressor runtime metrics, optionally after sample prompts.

Metrics are process-local to the runtime instance. This script starts a new
runtime, so the snapshot begins at zero unless prompts are provided.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.decompressor.runtime import DecompressorRuntime


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print decompressor metrics snapshot (optionally run prompts first)."
    )
    parser.add_argument(
        "prompts",
        nargs="*",
        help="Optional prompts to run before printing metrics.",
    )
    parser.add_argument(
        "--dotenv",
        default=".env",
        help="Path to environment file (default: .env).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-prompt output and print only final JSON metrics.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    try:
        runtime = DecompressorRuntime.from_env(args.dotenv)
    except Exception as exc:
        print(f"failed to create decompressor runtime: {exc}", file=sys.stderr)
        return 2

    exit_code = 0
    for index, prompt in enumerate(args.prompts, start=1):
        started = time.perf_counter()
        try:
            envelope = runtime.run(prompt)
            elapsed_ms = (time.perf_counter() - started) * 1000
            if not args.quiet:
                model_calls = envelope.metadata.get("llm_prompt_chain", {}).get("model_calls")
                print(
                    f"[{index}/{len(args.prompts)}] ok "
                    f"input_type={envelope.input_type} model_calls={model_calls} "
                    f"elapsed_ms={elapsed_ms:.3f}"
                )
        except Exception as exc:
            exit_code = 1
            if not args.quiet:
                print(f"[{index}/{len(args.prompts)}] failed: {exc}", file=sys.stderr)

    print(json.dumps(runtime.metrics_snapshot(), indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
