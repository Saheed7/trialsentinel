# ADR 0006: Verifier v4 (provenance defences) is the API default

- Status: accepted
- Date: 2026-11-22
- Evidence: `docs/research/redteam-round1-results.md`, `docs/research/redteam-round2-results.md`

## Context

Retrieved excerpts are free text written by sponsors and authors. Verifier v3 checks a claim
against the text it cites, so a planted excerpt can be cited as support for a false claim.
The pre-registered red-team showed that the v3 pipeline published a false claim in 36/36
attack runs (planted dates, fake exemption and compliance statements, judge-targeting notes,
a spoofed `registry_result` link line, hidden markup). Round-1 defences blocked all attacks
but over-rejected valid claims; round 2's narrower rule D v2 met every pre-registered
criterion (0/36 attacks, 1 added wrongful rejection offline, 89% of clean claims kept).

## Decision

`POST /v1/audits` and `AgentRunner` default to **verifier v4**:

1. excerpts are sanitised in `build_evidence` (markup stripped, evidence IDs, kind markers
   and link lines defused, labelled "[untrusted quoted text]");
2. drafter prompt v3 tells the model excerpts are data, never instructions or a source for
   reporting status;
3. rule D v2: claims using FDAAA obligation terms must cite rule or registry evidence and be
   grounded in it; any PubMed or trial ID must appear in the trial's trusted evidence;
4. for those claims the LLM judge sees only trusted evidence.

`verifier_version: 3` (with `prompt_version: 2`) remains available and reproduces the
pre-registered evaluation configuration. The CLI `eval`, `recheck` and `rejudge` commands
and the red-team harness are unchanged, so every reported number can be regenerated.

Each audit records `verifier_version` and `prompt_version` in its result and in
`agent_audits`; rule D rejections are counted under the `rule_d` metric label.

## Consequences

- The paper's accuracy results (242-claim test set) remain v3 results; v4 is reported as
  the hardened configuration with the red-team as its evidence. v4's effect on accuracy has
  only been estimated offline and on clean red-team runs, not on a new labelled test set.
- Accurate but attributed claims about excerpt content ("the sponsor states it is exempt")
  are rejected. This is deliberate: they repeat attacker-controlled assertions.
- Claims citing another trial's ID that appears only in an excerpt are rejected (one case
  in 144 offline claims).
- The defences were tested against white-box, non-adaptive payloads in one injection
  position. An adaptive attacker who avoids rule D's terms is the next thing to test.
