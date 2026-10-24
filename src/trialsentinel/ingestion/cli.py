"""Ingestion command line.

Examples:
    uv run python -m trialsentinel.ingestion.cli ctgov --condition "asthma" --status COMPLETED
    uv run python -m trialsentinel.ingestion.cli ctgov-refresh
    uv run python -m trialsentinel.ingestion.cli pubmed-link --status COMPLETED --limit 75
    uv run python -m trialsentinel.ingestion.cli faers --status COMPLETED --limit 75 --max-drugs 10
"""

import argparse
import asyncio
from collections.abc import Sequence

from pydantic import BaseModel

from trialsentinel.core.config import get_settings
from trialsentinel.core.logging import configure_logging
from trialsentinel.db.session import get_engine
from trialsentinel.ingestion.faers_pipeline import ingest_faers
from trialsentinel.ingestion.pipeline import ingest_clinicaltrials
from trialsentinel.ingestion.pubmed_pipeline import link_pubmed
from trialsentinel.ingestion.refresh_pipeline import refresh_clinicaltrials


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trialsentinel-ingest")
    sub = parser.add_subparsers(dest="command", required=True)

    ct = sub.add_parser("ctgov", help="Discover studies on ClinicalTrials.gov by search")
    ct.add_argument("--condition")
    ct.add_argument("--intervention")
    ct.add_argument("--sponsor")
    ct.add_argument("--status", action="append", dest="statuses", help="Repeatable")
    ct.add_argument("--max-studies", type=int, default=100)
    ct.add_argument("--page-size", type=int, default=100)

    rf = sub.add_parser("ctgov-refresh", help="Re-fetch every stored trial by NCT ID")
    rf.add_argument("--limit", type=int, default=None)

    pm = sub.add_parser("pubmed-link", help="Link stored trials to PubMed publications")
    pm.add_argument("--status", action="append", dest="statuses", help="Repeatable")
    pm.add_argument("--limit", type=int, default=100)

    fa = sub.add_parser("faers", help="FAERS disproportionality for stored trials' drugs")
    fa.add_argument("--status", action="append", dest="statuses", help="Repeatable")
    fa.add_argument("--limit", type=int, default=75, help="Number of trials to draw drugs from")
    fa.add_argument("--max-drugs", type=int, default=10)
    fa.add_argument("--top-events", type=int, default=25)
    return parser


async def _dispatch(args: argparse.Namespace) -> BaseModel:
    if args.command == "ctgov":
        return await ingest_clinicaltrials(
            condition=args.condition,
            intervention=args.intervention,
            sponsor=args.sponsor,
            statuses=args.statuses,
            max_studies=args.max_studies,
            page_size=args.page_size,
        )
    if args.command == "ctgov-refresh":
        return await refresh_clinicaltrials(limit=args.limit)
    if args.command == "pubmed-link":
        return await link_pubmed(statuses=args.statuses or ["COMPLETED"], limit=args.limit)
    if args.command == "faers":
        return await ingest_faers(
            statuses=args.statuses or ["COMPLETED"],
            trial_limit=args.limit,
            max_drugs=args.max_drugs,
            top_events=args.top_events,
        )
    raise ValueError(f"unknown command: {args.command}")


async def _run(args: argparse.Namespace) -> None:
    try:
        summary = await _dispatch(args)
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
