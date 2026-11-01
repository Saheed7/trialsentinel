"""Offline re-verification of frozen claims, so verifier versions and judges are compared
on identical claims without re-drafting (reproducible, and fair across variants)."""

from collections.abc import Callable

from trialsentinel.agents.verifier import VERIFIER_VERSION, Claim, check_text

Judge = Callable[[Claim, str], bool]


def recheck(rows: list[dict[str, str]]) -> int:
    """Re-runs the current content checks; structural failures are kept as recorded."""
    changed = 0
    for row in rows:
        if row["deterministic"].startswith("cites "):
            continue
        reason = check_text(row["claim"], row["cited_text"], subject_ids=frozenset({row["nct_id"]}))
        new = reason or "pass"
        changed += new != row["deterministic"]
        row["deterministic"] = new
        row["verifier_version"] = str(VERIFIER_VERSION)
    return changed


def rejudge(rows: list[dict[str, str]], judge: Judge, column: str) -> int:
    """Writes one judge's verdict on every claim into `column`; returns rejections."""
    rejected = 0
    for row in rows:
        ok = judge(Claim(text=row["claim"]), row["cited_text"])
        row[column] = "supported" if ok else "unsupported"
        rejected += not ok
    return rejected
