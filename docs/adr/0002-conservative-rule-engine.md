# ADR-0002: Conservative, versioned rule engine for FDAAA 801

- Status: Accepted
- Date: 2026-10-05

## Context
Public registry data cannot establish FDAAA 801 applicability with certainty
(IND/IDE status is not public), registry dates are often partial, and
certification versus extension filings are indistinguishable.

## Decisionc
- Rules are pure functions over a `TrialFacts` value object: no I/O, fully unit-tested.
- A trial is flagged only when every uncertain input resolves against it: partial
  dates use their latest possible day; applicability requires positive evidence;
  outcomes are likely_act / not_act / undetermined, each with an explicit basis.
- Assessments are persisted per (trial, rule_version, as_of_date), so results are
  reproducible and rule changes are auditable.

## Consequences
- Lower recall by design; every finding is explainable from stored facts.
- Validation target: FDA-issued pre-notices and notices of noncompliance.
