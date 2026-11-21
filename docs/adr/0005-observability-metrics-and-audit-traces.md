# ADR 0005: Observability with Prometheus metrics and per-audit traces

- Status: accepted
- Date: 2026-11-08

## Context

The API runs audits that take minutes, call a local LLM several times and pass every claim
through a layered verifier. Without instrumentation we cannot answer basic operating
questions: how long audits take, where the time goes (retrieval vs. LLM), how many tokens an
audit costs, whether the queue is backing up, and how often each verifier layer rejects claims
in production compared with the evaluation set.

## Decision

1. **Prometheus metrics at `GET /metrics`** using `prometheus_client`, with one
   `CollectorRegistry` per app instance (so tests and multiple apps never share state).
   - HTTP: `trialsentinel_http_requests_total{method,route,status}` and
     `trialsentinel_http_request_duration_seconds{method,route}`.
   - Audits: `trialsentinel_audits_total{status}`, `trialsentinel_audit_duration_seconds`,
     `trialsentinel_audit_queue_depth` (read live from the job queue).
   - LLM: `trialsentinel_llm_calls_total`, `trialsentinel_llm_tokens_total{direction}`.
   - Verifier: `trialsentinel_verifier_decisions_total{layer,outcome}` and
     `trialsentinel_claims_total{decision}`.
2. **Bounded labels only.** The `route` label is the route template
   (`/v1/audits/{audit_id}`), never the raw path; verifier outcomes are collapsed to a fixed
   set (`pass`, `rule_b`, `rule_c`, `structure`, `token`, `other`). NCT IDs, audit IDs and
   API-key fingerprints never become label values.
3. **A per-audit trace** is stored with each result and returned in `result.trace`:
   retrieval time, agent time, LLM calls, LLM latency, input/output tokens, estimated prompt
   size, context-window warning, and decision counts per verifier layer. Token and call counts
   are deltas of the runner's cumulative `UsageCallback` totals, taken inside the runner lock,
   so concurrent requests cannot mix counts.
4. **One structured `audit_trace` log line** per finished audit, carrying the same fields, so
   logs alone are enough to reconstruct cost and behaviour.
5. **`/metrics` is unauthenticated** but contains no trial data or secrets. In a deployed
   environment it must be restricted at the network level (private subnet or security group).

## Consequences

- Production rejection rates per verifier layer can be compared with the pre-registered
  evaluation results, an early signal of drift.
- Token counts give a direct cost estimate when moving from local Ollama to a hosted model.
- Audits recorded before this change have `trace: null`; the schema allows it.
- Distributed tracing (OpenTelemetry spans across API, retriever and LLM) is **deferred** to
  the AWS deployment, where a collector and backend will exist. The trace dict is designed to
  map onto span attributes later.
- Grafana dashboards and alert rules are optional follow-ups (Prometheus + Grafana in
  `docker compose`).
