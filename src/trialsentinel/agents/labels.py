"""Scores verifier decisions against human labels: the ground truth for grounding metrics.

Positive class = an unsupported claim (a hallucination relative to its cited evidence)."""

import csv
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

LABEL_COLUMN = "human_supported"  # "yes" or "no"
FIELDS = [
    "nct_id",
    "round",
    "decision",
    "deterministic",
    "judge",
    "claim",
    "evidence_ids",
    "cited_text",
    LABEL_COLUMN,
]


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def _rejects(row: Mapping[str, str], layer: str) -> bool:
    if layer == "deterministic":
        return row["deterministic"] != "pass"
    if layer == "judge":
        return row["judge"] == "unsupported"
    return row["decision"] == "rejected"


def score(rows: Iterable[Mapping[str, str]]) -> dict[str, Any]:
    labelled = [r for r in rows if r.get(LABEL_COLUMN, "").strip().lower() in {"yes", "no"}]
    unsupported = [r for r in labelled if r[LABEL_COLUMN].strip().lower() == "no"]
    supported = [r for r in labelled if r[LABEL_COLUMN].strip().lower() == "yes"]
    layers = ["deterministic", "combined"]
    if any(r.get("judge", "n/a") != "n/a" for r in labelled):
        layers.insert(1, "judge")

    report: dict[str, Any] = {
        "claims_labelled": len(labelled),
        "draft_hallucination_rate": _rate(len(unsupported), len(labelled)),
    }
    for layer in layers:
        caught = sum(_rejects(r, layer) for r in unsupported)
        false_rejections = sum(_rejects(r, layer) for r in supported)
        accepted = sum(not _rejects(r, layer) for r in labelled)
        report[layer] = {
            "catch_rate": _rate(caught, len(unsupported)),
            "false_rejection_rate": _rate(false_rejections, len(supported)),
            "residual_hallucination_rate": _rate(len(unsupported) - caught, accepted),
        }
    return report


def write_rows(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))
