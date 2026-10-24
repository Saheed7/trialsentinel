"""LLM smoke test: structured extraction via tool calling, with usage accounting.

Example:
    uv run python -m trialsentinel.llm.cli ping
    uv run python -m trialsentinel.llm.cli ping --model llama3.1:8b
"""

import argparse
import json
import time
from collections.abc import Sequence

from pydantic import BaseModel, Field

from trialsentinel.core.config import get_settings
from trialsentinel.llm.factory import get_chat_model
from trialsentinel.llm.usage import UsageCallback

SAMPLES = [
    "Metformin 500 mg tablets + Sitagliptin",
    "Insulin glargine (Lantus) 10 IU subcutaneous once daily",
    "Matching placebo tablet",
    "Dapagliflozin 10mg and saline flush",
]


class DrugExtraction(BaseModel):
    """Active drug ingredients named in a trial intervention description."""

    drugs: list[str] = Field(
        description="Generic active ingredient names in lowercase; exclude placebo, saline, "
        "vehicle, and standard-of-care. Empty list if none."
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trialsentinel-llm")
    sub = parser.add_subparsers(dest="command", required=True)
    ping = sub.add_parser("ping", help="Structured-extraction smoke test")
    ping.add_argument("--model", default=None, help="Override TS_LLM_MODEL")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    if args.model:
        settings = settings.model_copy(update={"llm_model": args.model})

    tracker = UsageCallback()
    extractor = get_chat_model(settings).with_structured_output(
        DrugExtraction, method="function_calling"
    )
    results = []
    started = time.perf_counter()
    for text in SAMPLES:
        try:
            out = extractor.invoke(f"Intervention: {text}", config={"callbacks": [tracker]})
            results.append({"input": text, "drugs": out.drugs if out else None})
        except Exception as exc:  # report, don't crash: model quality is what we're measuring
            results.append({"input": text, "error": type(exc).__name__})

    t = tracker.totals
    report = {
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "results": results,
        "calls": t.calls,
        "input_tokens": t.input_tokens,
        "output_tokens": t.output_tokens,
        "wall_time_s": round(time.perf_counter() - started, 2),
        "output_tokens_per_s": round(t.output_tokens_per_s, 1),
        "cost_usd": round(
            t.cost_usd(settings.llm_price_input_per_mtok, settings.llm_price_output_per_mtok), 6
        ),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
