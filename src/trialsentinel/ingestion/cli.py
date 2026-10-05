"""Ingestion command line.

Examples:
    uv run python -m trialsentinel.ingestion.cli ctgov --condition "asthma" --status COMPLETED
    uv run python -m trialsentinel.ingestion.cli pubmed-link --status COMPLETED --limit 75
"""

import argparse
import asyncio
from collections.abc import Sequence

from trialsentinel.core.config import get_settings
from trialsentinel.core.logging import configure_logging
from trialsentinel.db.session import get_engine
from trialsentinel.ingestion.pipeline import ingest_clinicaltrials
from trialsentinel.ingestion.pubmed_pipeline import link_pubmed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trialsentinel-ingest")
    sub = parser.add_subparsers(dest="command", required=True)

    ct = sub.add_parser("ctgov", help="Ingest studies from ClinicalTrials.gov")
    ct.add_argument("--condition")
    ct.add_argument("--intervention")
    ct.add_argument("--sponsor")
    ct.add_argument("--status", action="append", dest="statuses", help="Repeatable")
    ct.add_argument("--max-studies", type=int, default=100)
    ct.add_argument("--page-size", type=int, default=100)

    pm = sub.add_parser("pubmed-link", help="Link stored trials to PubMed publications")
    pm.add_argument("--status", action="append", dest="statuses", help="Repeatable")
    pm.add_argument("--limit", type=int, default=100)
    return parser


async def _run(args: argparse.Namespace) -> None:
    try:
        if args.command == "ctgov":
            summary = await ingest_clinicaltrials(
                condition=args.condition,
                intervention=args.intervention,
                sponsor=args.sponsor,
                statuses=args.statuses,
                max_studies=args.max_studies,
                page_size=args.page_size,
            )
        else:
            summary = await link_pubmed(statuses=args.statuses or ["COMPLETED"], limit=args.limit)
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
