from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_openai import ChatOpenAI

from trialsentinel.core.config import Settings
from trialsentinel.llm.factory import get_chat_model
from trialsentinel.llm.usage import UsageCallback, UsageTotals


def test_factory_points_ollama_at_local_openai_compatible_endpoint() -> None:
    model = get_chat_model(Settings(llm_provider="ollama", llm_model="qwen2.5:7b"))
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "qwen2.5:7b"
    assert "localhost:11434" in str(model.openai_api_base)


def test_openai_provider_requires_a_key() -> None:
    with pytest.raises(RuntimeError, match="TS_OPENAI_API_KEY"):
        get_chat_model(Settings(llm_provider="openai", openai_api_key=None))


def test_usage_callback_accumulates_tokens_across_calls() -> None:
    tracker = UsageCallback()
    for _ in range(2):
        run_id = uuid4()
        tracker.on_chat_model_start({}, [], run_id=run_id)
        message = AIMessage(
            content="ok",
            usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        )
        tracker.on_llm_end(
            LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=run_id
        )
    assert tracker.totals.calls == 2
    assert tracker.totals.input_tokens == 200
    assert tracker.totals.output_tokens == 40
    assert len(tracker.totals.latencies) == 2


def test_cost_uses_configured_prices_and_is_zero_for_local() -> None:
    totals = UsageTotals(input_tokens=2_000_000, output_tokens=500_000)
    assert totals.cost_usd(0.0, 0.0) == 0.0
    assert totals.cost_usd(1.0, 4.0) == pytest.approx(4.0)  # 2 x $1 + 0.5 x $4
