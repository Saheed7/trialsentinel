# ADR 0003: API with asynchronous audit jobs and hashed API keys

Status: accepted (2026-11-02)

## Context
An audit runs a local 8B model and takes about 1-2 minutes on a 4 GB GPU. A synchronous HTTP
request would hold a connection open for minutes and time out behind most proxies. Clients also
need authentication, and the service must never expose or store usable credentials.

## Decision
- `POST /v1/audits` validates the request, loads the case on the app's event loop, queues the
  audit and returns `202 Accepted` with a `Location` header. `GET /v1/audits/{id}` returns the
  status and, when finished, the verified claims, rejections, evidence and full audit trail.
- One worker thread runs audits in order (one GPU, one loaded model). A bounded queue returns
  `429` with `Retry-After` when full, instead of growing without limit.
- Rule-engine data (`/v1/findings`, `/v1/trials/{nct_id}/assessment`) is served directly: it is
  deterministic and fast.
- Clients send `X-API-Key`. Only SHA-256 hashes of keys are configured (`TS_API_KEYS_SHA256`),
  compared in constant time. With no keys configured, protected endpoints fail closed (`503`).
- `/healthz` (liveness) and `/readyz` (database reachable) are public, for orchestrators.
- Every response carries `X-Request-ID`; each request is logged with method, path, status and latency.

## Consequences
- Jobs are held in memory: they are lost on restart and not shared across processes. This is
  acceptable for one local GPU; a durable store (PostgreSQL for the audit trail, Redis for the
  queue) is the next step and will keep the same API contract.
- The agent code is shared with the CLI (`agents/service.py`), so the API and the evaluation
  harness run the identical pipeline.
