"""AppV2 prompt decomposition runtime."""

from __future__ import annotations

import itertools
import time
from typing import Any

from appV2.decomposer.contracts import PromptChainModelClient
from appV2.decomposer.prompt_chain import DecomposerPromptChain
from appV2.env_config import build_appv2_model_client
from appV2.runtime_matrix import RuntimeMatrixLogger, attach_runtime_matrix, coerce_runtime_matrix
from appV2.schemas import Envelope


_REQUEST_COUNTER = itertools.count(1)


class DecomposerRuntime:
    def __init__(
        self,
        *,
        model_client: PromptChainModelClient | None = None,
        prompt_chain: Any | None = None,
    ) -> None:
        if prompt_chain is not None:
            self._prompt_chain = prompt_chain
        elif model_client is not None:
            self._prompt_chain = DecomposerPromptChain(model_client=model_client)
        else:
            raise ValueError("DecomposerRuntime requires a model_client or prompt_chain")

    @classmethod
    def from_env(cls, dotenv_path: str = ".env", **client_options: Any) -> "DecomposerRuntime":
        model_client = build_appv2_model_client("APPV2_DECOMPOSER_LLM", dotenv_path, **client_options)
        if model_client is None:
            raise ValueError("AppV2 decomposer is not configured. Set APPV2_DECOMPOSER_LLM_ENABLED=true.")
        return cls(model_client=model_client)

    def run(self, user_input: str, *, trace: RuntimeMatrixLogger | None = None) -> Envelope:
        request_id = f"v2_req_{next(_REQUEST_COUNTER):03d}"
        trace = coerce_runtime_matrix(trace)
        started = time.perf_counter()
        trace.record(
            component="appv2_decomposer_runtime",
            stage="decompose_request",
            event="run_started",
            status="started",
            request_id=request_id,
            details={"input_chars": len(user_input or "")},
        )
        try:
            envelope = self._prompt_chain.run(user_input or "", request_id, trace=trace)
        except Exception as exc:
            trace.record(
                component="appv2_decomposer_runtime",
                stage="decompose_request",
                event="run_failed",
                status="failed",
                request_id=request_id,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                details={"error": str(exc)},
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        trace.record(
            component="appv2_decomposer_runtime",
            stage="decompose_request",
            event="run_completed",
            status="completed",
            request_id=request_id,
            elapsed_ms=elapsed_ms,
            details={
                "input_type": envelope.input_type,
                "confidence": envelope.confidence,
                "ambiguity_count": len(envelope.ambiguity),
            },
        )
        metadata = dict(envelope.metadata)
        metadata["appv2_decomposer_runtime"] = {"elapsed_ms": round(elapsed_ms, 3)}
        metadata = attach_runtime_matrix(metadata, trace)
        return envelope.model_copy(update={"metadata": metadata})
