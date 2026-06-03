"""LLM-only decompression into `Envelope` objects."""

from __future__ import annotations

import math
import itertools
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from app.decompressor.contracts import PromptChainModelClient
from app.decompressor.env_config import build_decompressor_model_client
from app.decompressor.prompt_chain import LLMPromptChainDecompressor
from app.runtime_matrix import RuntimeMatrixLogger, attach_runtime_matrix, coerce_runtime_matrix
from app.schemas import Envelope


_REQUEST_COUNTER = itertools.count(1)
_LATENCY_SAMPLE_WINDOW = 200


@dataclass(slots=True)
class _RuntimeMetrics:
    successful_runs: int = 0
    failed_runs: int = 0
    repair_runs: int = 0
    latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=_LATENCY_SAMPLE_WINDOW))
    lock: Lock = field(default_factory=Lock)

    def record_success(self, *, elapsed_ms: float, repaired: bool) -> None:
        with self.lock:
            self.successful_runs += 1
            if repaired:
                self.repair_runs += 1
            self.latencies_ms.append(elapsed_ms)

    def record_failure(self, *, elapsed_ms: float) -> None:
        with self.lock:
            self.failed_runs += 1
            self.latencies_ms.append(elapsed_ms)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            successful_runs = self.successful_runs
            failed_runs = self.failed_runs
            repair_runs = self.repair_runs
            latencies = list(self.latencies_ms)

        total_runs = successful_runs + failed_runs
        return {
            "total_runs": total_runs,
            "successful_runs": successful_runs,
            "failed_runs": failed_runs,
            "repair_runs": repair_runs,
            "failure_rate": failed_runs / total_runs if total_runs else 0.0,
            "repair_rate": repair_runs / successful_runs if successful_runs else 0.0,
            "latency_window_size": len(latencies),
            "latency_ms_p50": _percentile(latencies, 0.5),
            "latency_ms_p95": _percentile(latencies, 0.95),
        }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(ordered[lower], 3)
    lower_value = ordered[lower]
    upper_value = ordered[upper]
    interpolated = lower_value + (upper_value - lower_value) * (rank - lower)
    return round(interpolated, 3)


class DecompressorRuntime:
    """Runs an injected LLM prompt chain and returns a validated Envelope.

    This runtime intentionally has no deterministic or heuristic Envelope builder.
    Prompt-chain stages are responsible for understanding the request; this class
    owns request IDs and the stable runtime boundary only.
    """

    def __init__(
        self,
        model_client: PromptChainModelClient | None = None,
        prompt_chain: Any | None = None,
    ) -> None:
        self._metrics = _RuntimeMetrics()
        if prompt_chain is not None:
            self._prompt_chain = prompt_chain
        elif model_client is not None:
            self._prompt_chain = LLMPromptChainDecompressor(model_client=model_client)
        else:
            raise ValueError("DecompressorRuntime requires an LLM model_client or prompt_chain.")

    @classmethod
    def from_env(cls, dotenv_path: str = ".env", **client_options: Any) -> "DecompressorRuntime":
        """Create an LLM-only runtime from configured environment values."""

        model_client = build_decompressor_model_client(dotenv_path, **client_options)
        if model_client is None:
            raise ValueError("LLM decompressor is not configured. Set DECOMPRESSOR_LLM_ENABLED=true.")
        return cls(model_client=model_client)

    def run(self, user_input: str, *, trace: RuntimeMatrixLogger | None = None) -> Envelope:
        started = time.perf_counter()
        request_id = f"req_{next(_REQUEST_COUNTER):03d}"
        trace = coerce_runtime_matrix(trace)
        trace.record(
            component="decompressor_runtime",
            stage="decompress_request",
            event="run_started",
            status="started",
            request_id=request_id,
            details={"input_chars": len(user_input or "")},
        )
        try:
            envelope = self._prompt_chain.run(user_input or "", request_id)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self._metrics.record_failure(elapsed_ms=elapsed_ms)
            trace.record(
                component="decompressor_runtime",
                stage="decompress_request",
                event="run_failed",
                status="failed",
                request_id=request_id,
                elapsed_ms=elapsed_ms,
                details={"error": str(exc)},
            )
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000
        raw_model_calls = envelope.metadata.get("llm_prompt_chain", {}).get("model_calls", 1)
        try:
            model_calls = int(raw_model_calls)
        except (TypeError, ValueError):
            model_calls = 1
        model_calls = max(1, model_calls)
        self._metrics.record_success(elapsed_ms=elapsed_ms, repaired=model_calls > 1)
        trace.record(
            component="decompressor_runtime",
            stage="decompress_request",
            event="run_completed",
            status="completed",
            request_id=request_id,
            elapsed_ms=elapsed_ms,
            details={
                "model_calls": model_calls,
                "confidence": envelope.confidence,
                "ambiguity_count": len(envelope.ambiguity),
            },
        )

        snapshot = self._metrics.snapshot()
        metadata = dict(envelope.metadata)
        metadata["decompressor_runtime"] = {
            "elapsed_ms": round(elapsed_ms, 3),
            "failure_rate": snapshot["failure_rate"],
            "repair_rate": snapshot["repair_rate"],
            "latency_ms_p50": snapshot["latency_ms_p50"],
            "latency_ms_p95": snapshot["latency_ms_p95"],
        }
        metadata = attach_runtime_matrix(metadata, trace)
        return envelope.model_copy(update={"metadata": metadata})

    def metrics_snapshot(self) -> dict[str, Any]:
        """Return bounded runtime observability counters and latency percentiles."""

        return self._metrics.snapshot()
