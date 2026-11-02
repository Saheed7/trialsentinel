# ADR 0004: Durable audit records in PostgreSQL

Status: accepted (2026-11-02). Supersedes the in-memory limitation noted in ADR 0003.

## Context
Audits explain regulatory findings, so their record must outlive the process that produced
them: which trial, which options, which verifier version, what was claimed, what was rejected,
and who asked. In-memory jobs were lost on restart.

## Decision
- A new `agent_audits` table stores each audit's options, status, timestamps, error, full
  result (claims, rejections, evidence, per-claim audit trail), verifier version and the
  requesting key's fingerprint (never the key).
- Every job state change (queued, running, finished) is written. Writes are scheduled from the
  worker thread onto the app's event loop, so they can arrive out of order; each carries a rising
  `revision`, and the upsert applies only when the stored revision is lower.
- Reads check memory first (fast polling of live audits), then PostgreSQL.
- At startup, audits left `queued` or `running` by a previous process are marked `failed` with
  an explicit "interrupted" reason, so no record claims to be running forever.
- `GET /v1/trials/{nct_id}/audits` returns a trial's audit history, newest first.
- A database outage does not stop audits: the failed write is logged and the result is still
  served from memory.

## Consequences
- The queue itself is still in memory: audits waiting when the process stops are marked
  interrupted rather than resumed. A durable queue (Redis) can replace it later without
  changing the table or the API.
