"""Human labelling and scoring of verifier decisions: the ground truth for grounding metrics.

Positive class = an unsupported claim (a hallucination relative to its cited evidence)."""

import csv
from collections.abc import Callable, Iterable, Mapping
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
_LABELS = {"yes", "no"}


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def _label(row: Mapping[str, str]) -> str:
    return row.get(LABEL_COLUMN, "").strip().lower()


def _rejects(row: Mapping[str, str], layer: str) -> bool:
    if layer == "deterministic":
        return row["deterministic"] != "pass"
    if layer == "judge":
        return row["judge"] == "unsupported"
    return row["decision"] == "rejected"


def score(rows: Iterable[Mapping[str, str]]) -> dict[str, Any]:
    labelled = [r for r in rows if _label(r) in _LABELS]
    unsupported = [r for r in labelled if _label(r) == "no"]
    supported = [r for r in labelled if _label(r) == "yes"]
    layers = ["deterministic", "combined"]
    if any(r.get("judge", "n/a") != "n/a" for r in labelled):
        layers.insert(1, "judge")

    report: dict[str, Any] = {
        "claims_labelled": len(labelled),
        "draft_hallucination_rate": _rate(len(unsupported), len(labelled)),
    }
    if not labelled:
        report["hint"] = "no labels yet: run the 'label' command first"

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
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_rows(path: Path) -> list[dict[str, str]]:
    """Reads a labelled CSV, refusing files that would be silently misread."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        duplicates = sorted({name for name in header if header.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate column(s) {duplicates} in {path}; keep only one of each")
        if LABEL_COLUMN not in header:
            raise ValueError(f"missing '{LABEL_COLUMN}' column in {path}")
        return [dict(zip(header, row, strict=False)) for row in reader]


def interactive_label(
    path: Path,
    *,
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
) -> dict[str, int]:
    """Blind labelling: shows only the claim and its cited evidence, never the verifier's
    decision. Saves after every answer, so it can be stopped and resumed safely."""
    rows = load_rows(path)
    todo = [i for i, r in enumerate(rows) if _label(r) not in _LABELS]
    show(
        f"{len(todo)} of {len(rows)} claims need a label.\n"
        "Answer y if the cited evidence ALONE fully supports every part of the claim, "
        "n if anything is missing or contradicted, s to skip, q to quit (progress is saved)."
    )
    labelled_now = 0
    for position, index in enumerate(todo, start=1):
        row = rows[index]
        show(
            f"\n[{position}/{len(todo)}] CLAIM:\n  {row['claim']}\n"
            f"CITED EVIDENCE:\n  {row['cited_text'] or '(none cited)'}"
        )
        answer = ""
        while answer not in {"y", "n", "s", "q"}:
            answer = ask("Supported? [y/n/s/q]: ").strip().lower()
        if answer == "q":
            break
        if answer == "s":
            continue
        row[LABEL_COLUMN] = "yes" if answer == "y" else "no"
        write_rows(path, rows)
        labelled_now += 1
    return {
        "labelled_now": labelled_now,
        "labelled_total": sum(_label(r) in _LABELS for r in rows),
        "rows": len(rows),
    }
