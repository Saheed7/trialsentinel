"""Token, latency, and cost accounting for every LLM call (feeds cost optimisation)."""

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult


@dataclass
class UsageTotals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    latencies: list[float] = field(default_factory=list)

    def cost_usd(self, price_in_per_mtok: float, price_out_per_mtok: float) -> float:
        return (
            self.input_tokens * price_in_per_mtok + self.output_tokens * price_out_per_mtok
        ) / 1_000_000

    @property
    def output_tokens_per_s(self) -> float:
        return self.output_tokens / self.latency_s if self.latency_s else 0.0


class UsageCallback(BaseCallbackHandler):
    """Attach via `config={"callbacks": [tracker]}`; accumulates usage across calls."""

    def __init__(self) -> None:
        self.totals = UsageTotals()
        self._started: dict[UUID, float] = {}

    def on_chat_model_start(
        self, serialized: Any, messages: Any, *, run_id: UUID, **kw: Any
    ) -> None:
        self._started[run_id] = time.perf_counter()

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kw: Any) -> None:
        started = self._started.pop(run_id, None)
        if started is not None:
            elapsed = time.perf_counter() - started
            self.totals.latency_s += elapsed
            self.totals.latencies.append(elapsed)
        self.totals.calls += 1
        for generations in response.generations:
            for generation in generations:
                usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
                if usage:
                    self.totals.input_tokens += int(usage.get("input_tokens", 0))
                    self.totals.output_tokens += int(usage.get("output_tokens", 0))
