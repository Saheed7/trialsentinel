"""LLM smoke evaluation: structured drug extraction, scored against expected answers.

Examples:
    uv run python -m trialsentinel.llm.cli ping --model qwen2.5:7b
    uv run python -m trialsentinel.llm.cli ping --model qwen2.5:3b --method json_schema
"""

import argparse
import json
import time
from collections.abc import Sequence

from pydantic import BaseModel, Field

from trialsentinel.core.config import get_settings
from trialsentinel.guardrails.drug_terms import drop_non_drug_terms
from trialsentinel.llm.factory import get_chat_model
from trialsentinel.llm.usage import UsageCallback

# (intervention text, expected active ingredients)
CASES: list[tuple[str, list[str]]] = [
    ("Metformin 500 mg tablets + Sitagliptin", ["metformin", "sitagliptin"]),
    ("Insulin glargine (Lantus) 10 IU subcutaneous once daily", ["insulin glargine"]),
    ("Matching placebo tablet", []),
    ("Dapagliflozin 10mg and saline flush", ["dapagliflozin"]),
]


class DrugExtraction(BaseModel):
    """Active drug ingredients named in a trial intervention description."""

    drugs: list[str] = Field(
        default_factory=list,
        description="Generic active ingredient names in lowercase; exclude placebo, saline, "
        "vehicle, and standard-of-care. Use an empty list if there are none.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trialsentinel-llm")
    sub = parser.add_subparsers(dest="command", required=True)
    ping = sub.add_parser("ping", help="Structured-extraction smoke evaluation")
    ping.add_argument("--model", default=None, help="Override TS_LLM_MODEL")
    ping.add_argument(
        "--method",
        choices=["function_calling", "json_schema"],
        default="function_calling",
        help="function_calling = model emits a tool call; json_schema = constrained decoding",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    if args.model:
        settings = settings.model_copy(update={"llm_model": args.model})

    tracker = UsageCallback()
    extractor = get_chat_model(settings).with_structured_output(DrugExtraction, method=args.method)
    results = []
    valid = correct_raw = correct_guarded = 0
    started = time.perf_counter()
    for text, expected in CASES:
        row: dict[str, object] = {"input": text, "expected": expected}
        try:
            out = extractor.invoke(f"Intervention: {text}", config={"callbacks": [tracker]})
            raw = out.drugs if out else []
            guarded = drop_non_drug_terms(raw)
            valid += 1
            correct_raw += set(raw) == set(expected)
            correct_guarded += set(guarded) == set(expected)
            row |= {"raw": raw, "guarded": guarded}
        except Exception as exc:  # model quality is what we're measuring
            row["error"] = type(exc).__name__
        results.append(row)

    t = tracker.totals
    n = len(CASES)
    report = {
        "model": settings.llm_model,
        "method": args.method,
        "valid_outputs": f"{valid}/{n}",
        "accuracy_raw": f"{correct_raw}/{n}",
        "accuracy_guarded": f"{correct_guarded}/{n}",
        "results": results,
        "input_tokens": t.input_tokens,
        "output_tokens": t.output_tokens,
        "wall_time_s": round(time.perf_counter() - started, 2),
        "cost_usd": round(
            t.cost_usd(settings.llm_price_input_per_mtok, settings.llm_price_output_per_mtok), 6
        ),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
