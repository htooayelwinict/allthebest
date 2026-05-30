#!/usr/bin/env python3
"""Smoke test various prompts through the live decompressor to evaluate Envelope quality."""

import json
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.decompressor.runtime import DecompressorRuntime
from app.schemas import Envelope


TEST_PROMPTS = [
    "what is docker",
    "fix payment_service.py",
    "fix the app",
    "fix terraform apply error",
    "do we have lighthouse sdk if we do, use it as async function to connect all transation apis and fix lagging issues",
    "it",
    "add dark mode to the settings page",
    "why is the database slow",
]


def run_smoke_test() -> None:
    """Run all test prompts and print Envelope quality."""
    runtime = DecompressorRuntime.from_env()
    success_count = 0
    failure_count = 0

    print("=" * 80)
    print("DECOMPRESSOR ENVELOPE QUALITY SMOKE TEST")
    print("=" * 80)
    print()

    for i, prompt in enumerate(TEST_PROMPTS, 1):
        print(f"[{i}/{len(TEST_PROMPTS)}] Testing: {prompt!r}")
        print("-" * 80)
        sys.stdout.flush()

        try:
            start_time = time.time()
            envelope = runtime.run(prompt)
            elapsed = time.time() - start_time
            success_count += 1

            print(f"✓ Envelope generated successfully in {elapsed:.2f}s")
            print()
            print("ENVELOPE DETAILS:")
            print(f"  request_id: {envelope.request_id}")
            print(f"  normalized_input: {envelope.normalized_input!r}")
            print(f"  user_goal: {envelope.user_goal!r}")
            print(f"  input_type: {envelope.input_type!r}")
            print(f"  intents: {envelope.intents}")
            print(f"  domains: {envelope.domains}")
            print(f"  risks: {envelope.risks}")
            print(f"  artifacts: {json.dumps(envelope.artifacts, indent=4)}")
            print(f"  context_needed: {envelope.context_needed}")
            print(f"  constraints: {envelope.constraints}")
            print(f"  complexity_hint: {envelope.complexity_hint!r}")
            print(f"  confidence: {envelope.confidence}")
            print(f"  ambiguity: {envelope.ambiguity}")
            print(f"  assumptions: {envelope.assumptions}")
            print(f"  metadata: {json.dumps(envelope.metadata, indent=4)}")

        except Exception as e:
            failure_count += 1
            print(f"✗ Failed to generate Envelope: {type(e).__name__}: {e}")

        print()
        print("=" * 80)
        print()
        sys.stdout.flush()

    print("SMOKE TEST SUMMARY")
    print("-" * 80)
    print(f"successful_prompts: {success_count}")
    print(f"failed_prompts: {failure_count}")
    print(f"runtime_metrics: {json.dumps(runtime.metrics_snapshot(), indent=4)}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Run specific prompts from command line
        TEST_PROMPTS = sys.argv[1:]
    run_smoke_test()
