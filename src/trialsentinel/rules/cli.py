"""Rule-engine command line.

Example:
    uv run python -m trialsentinel.rules.cli fdaaa --as-of 2026-10-05
"""

import argparse
import asyncio
from collections.abc import Sequence
from datetime import UTC, date, datetime

from trialsentinel.core.config import get_settings
from trialsentinel.core.logging import configure_logging
from trialsentinel.db.session import get_engine
from trialsentinel.rules.engine import evaluate_fdaaa


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trialsentinel-rules")
    sub = parser.add_subparsers(dest="command", required=True)
    fd = sub.add_parser("fdaaa", help="Evaluate FDAAA 801 applicability and reporting status")
    fd.add_argument("--as-of", type=date.fromisoformat, default=None, help="YYYY-MM-DD")
    return parser


async def _run(args: argparse.Namespace) -> None:
    try:
        as_of = args.as_of or datetime.now(UTC).date()
        summary = await evaluate_fdaaa(as_of=as_of)
        print(summary.model_dump_json(indent=2))
    finally:
        await get_engine().dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.env != "local")
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
