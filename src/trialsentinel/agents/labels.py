"""Human labelling and scoring of verifier decisions: the ground truth for grounding metrics.

Positive class = an unsupported claim (a hallucination relative to its cited evidence)."""

import csv
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

LABEL_COLUMN = "human_supported"
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


def judge_columns(rows: list[Mapping[str, str]]) -> list[str]:
    names = sorted({k for r in rows for k in r if k == "judge" or k.startswith("judge_")})
    return [c for c in names if any(r.get(c, "n/a") not in {"n/a", ""} for r in rows)]


def _rejects(row: Mapping[str, str], layer: str) -> bool:
    if "+" in layer:
        return any(_rejects(row, part) for part in layer.split("+"))
    if layer == "deterministic":
        return row["deterministic"] != "pass"
    if layer == "pipeline":
        return row["decision"] == "rejected"
    return row.get(layer) == "unsupported"


def score(rows: Iterable[Mapping[str, str]]) -> dict[str, Any]:
    all_rows = list(rows)
    labelled = [r for r in all_rows if _label(r) in _LABELS]
    unsupported = [r for r in labelled if _label(r) == "no"]
    supported = [r for r in labelled if _label(r) == "yes"]
    judges = judge_columns(labelled)
    layers = ["deterministic", *judges, *(f"deterministic+{j}" for j in judges)]

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
    rows = list(rows)
    extras = sorted({k for r in rows for k in r} - set(FIELDS))
    fieldnames = [*FIELDS[:-1], *extras, LABEL_COLUMN]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        duplicates = sorted({name for name in header if header.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate column(s) {duplicates} in {path}; keep only one of each")
        if LABEL_COLUMN not in header:
            raise ValueError(f"missing '{LABEL_COLUMN}' column in {path}")
        return [dict(zip(header, row, strict=False)) for row in reader]


PROTOCOL_REMINDER = (
    "Labelling protocol v1 (docs/labelling-protocol.md). Use ONLY the cited evidence.\n"
    "  y = every part of the claim is stated or paraphrased in the cited evidence\n"
    "  n = any part is missing from, or contradicted by, the cited evidence\n"
    "  1. A date not in the cited evidence (e.g. an added 'as of <date>') -> n\n"
    "  2. 'Completed on <primary completion date>' -> n; 'primary completion date was' -> y\n"
    "  3. 'Published' or 'reports the results' from a link alone -> n; 'is linked' -> y\n"
    "  4. Comparisons must keep the direction stated in the evidence\n"
    "  5. Same meaning in other words is fine ('results posted: no' -> 'not posted' = y)\n"
    "Tip: if the claim copies the evidence word for word, the answer is almost always y.\n"
    "s = skip (undecidable in about a minute), q = quit (progress is saved)."
)


def _key(row: Mapping[str, str]) -> tuple[str, str]:
    return (row["claim"].strip(), row["cited_text"].strip())


def interactive_label(
    path: Path,
    *,
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
) -> dict[str, int]:
    """Blind labelling: shows only the claim and its cited evidence, never any verifier
    decision. Identical claim+evidence pairs are asked once and labelled together, so the
    same item can never receive two different labels. Saves after every answer."""
    rows = load_rows(path)
    groups: dict[tuple[str, str], list[int]] = {}
    for i, r in enumerate(rows):
        groups.setdefault(_key(r), []).append(i)
    todo = [idx for idx in groups.values() if any(_label(rows[i]) not in _LABELS for i in idx)]
    show(f"{len(todo)} distinct claims need a label ({len(rows)} rows).\n{PROTOCOL_REMINDER}")
    labelled_now = 0
    for position, indices in enumerate(todo, start=1):
        row = rows[indices[0]]
        if position > 1 and position % 10 == 1:
            show(f"\n--- reminder ---\n{PROTOCOL_REMINDER}")
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
        for i in indices:
            rows[i][LABEL_COLUMN] = "yes" if answer == "y" else "no"
        write_rows(path, rows)
        labelled_now += 1
    return {
        "labelled_now": labelled_now,
        "labelled_total": sum(_label(r) in _LABELS for r in rows),
        "rows": len(rows),
    }


def clear_labels(path: Path) -> int:
    """Removes every human label (for a full blind re-label); returns rows cleared."""
    rows = load_rows(path)
    cleared = sum(_label(r) in _LABELS for r in rows)
    for r in rows:
        r[LABEL_COLUMN] = ""
    write_rows(path, rows)
    return cleared


# Calibration: made-up practice items (fictional trial IDs, never evaluation data) with
# known answers under protocol v1. An annotator must pass before labelling real claims.
CALIBRATION: list[dict[str, str]] = [
    {
        "claim": "The trial NCT09990001 had a primary completion date of 2019-06-30.",
        "cited": "Registry record NCT09990001: overall status COMPLETED; "
        "primary completion 2019-06-30 (ACTUAL); results posted: no.",
        "answer": "y",
        "why": "The date is copied exactly from the evidence (rule 5).",
    },
    {
        "claim": "No results were posted for NCT09990001 as of 2026-10-05.",
        "cited": "Registry record NCT09990001: overall status COMPLETED; "
        "primary completion 2019-06-30 (ACTUAL); results posted: no.",
        "answer": "n",
        "why": "The date 2026-10-05 is not in the cited evidence (rule 1).",
    },
    {
        "claim": "The trial NCT09990001 was completed on 2019-06-30.",
        "cited": "Registry record NCT09990001: overall status COMPLETED; "
        "primary completion 2019-06-30 (ACTUAL); results posted: no.",
        "answer": "n",
        "why": "2019-06-30 is the PRIMARY completion date, not the completion date (rule 2).",
    },
    {
        "claim": "The trial NCT09990001 has not posted its results.",
        "cited": "Registry record NCT09990001: overall status COMPLETED; "
        "primary completion 2019-06-30 (ACTUAL); results posted: no.",
        "answer": "y",
        "why": "'results posted: no' means the same thing in other words (rule 5).",
    },
    {
        "claim": "The trial NCT09990002 was published in a journal article (PubMed ID 90000001).",
        "cited": "PubMed 90000001 is linked to NCT09990002 via pubmed_si, registry_derived; "
        "published 2021-03-01; title: A cohort study of drug X.",
        "answer": "n",
        "why": "A link alone does not show the paper publishes this trial (rule 3).",
    },
    {
        "claim": "The publication 90000001 is linked to the trial NCT09990002.",
        "cited": "PubMed 90000001 is linked to NCT09990002 via pubmed_si, registry_derived; "
        "published 2021-03-01; title: A cohort study of drug X.",
        "answer": "y",
        "why": "The evidence states the link exactly (rule 3: 'is linked' is fine).",
    },
    {
        "claim": "The trial NCT09990003 reported its results 30 days late.",
        "cited": "FDAAA 801 rule assessment of NCT09990003 as of 2026-10-05: "
        "reporting status reported_late; days overdue n/a; days late 3.",
        "answer": "n",
        "why": "The evidence says 3 days late, not 30 (numbers must match).",
    },
    {
        "claim": "Arm B enrolled more participants than arm A.",
        "cited": "Excerpt from trial NCT09990004: 120 participants were enrolled in arm A "
        "and 80 in arm B.",
        "answer": "n",
        "why": "The comparison is reversed: arm A (120) enrolled more (rule 4).",
    },
    {
        "claim": "No publication is linked to the trial NCT09990005.",
        "cited": "No PubMed publication is linked to NCT09990005 by any route.",
        "answer": "y",
        "why": "Same meaning as the evidence (rule 5).",
    },
    {
        "claim": "The trial NCT09990003 was overdue for 412 days.",
        "cited": "FDAAA 801 rule assessment of NCT09990003 as of 2026-10-05: "
        "reporting status overdue; days overdue 412; days late n/a.",
        "answer": "y",
        "why": "The number is copied exactly from the evidence.",
    },
]
CALIBRATION_PASS = 9  # of 10


def calibrate(
    *,
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Runs the practice items, explaining each answer; returns the score and pass/fail."""
    show(f"CALIBRATION: {len(CALIBRATION)} practice items (made-up trials).\n{PROTOCOL_REMINDER}")
    correct = 0
    for position, item in enumerate(CALIBRATION, start=1):
        show(
            f"\n[practice {position}/{len(CALIBRATION)}] CLAIM:\n  {item['claim']}\n"
            f"CITED EVIDENCE:\n  {item['cited']}"
        )
        answer = ""
        while answer not in {"y", "n"}:
            answer = ask("Supported? [y/n]: ").strip().lower()
        right = answer == item["answer"]
        correct += right
        expected = "y (supported)" if item["answer"] == "y" else "n (not supported)"
        show(f"{'Correct.' if right else 'Not quite.'} Answer: {expected}. {item['why']}")
    passed = correct >= CALIBRATION_PASS
    show(
        f"\nScore {correct}/{len(CALIBRATION)}: "
        + ("passed, ready to label." if passed else "re-read the protocol and try again.")
    )
    return {"correct": correct, "items": len(CALIBRATION), "passed": passed}
