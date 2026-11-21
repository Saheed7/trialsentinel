"""Prometheus metrics for the API, the audit queue, the LLM and the verifier.

Each app owns its registry, so several apps (as in tests) never collide. Labels are drawn
from small fixed sets (route templates, statuses, decision categories) to keep cardinality low:
no trial IDs, audit IDs or API keys ever become label values."""

from collections.abc import Callable
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
AUDIT_BUCKETS = (5.0, 15.0, 30.0, 60.0, 90.0, 120.0, 180.0, 300.0, 600.0)


class Metrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.http_requests = Counter(
            "trialsentinel_http_requests",
            "HTTP requests by method, route template and status code",
            ["method", "route", "status"],
            registry=r,
        )
        self.http_latency = Histogram(
            "trialsentinel_http_request_duration_seconds",
            "HTTP request latency by method and route template",
            ["method", "route"],
            buckets=LATENCY_BUCKETS,
            registry=r,
        )
        self.audits = Counter(
            "trialsentinel_audits", "Finished audits by final status", ["status"], registry=r
        )
        self.audit_duration = Histogram(
            "trialsentinel_audit_duration_seconds",
            "Wall-clock time from audit start to finish",
            buckets=AUDIT_BUCKETS,
            registry=r,
        )
        self.queue_depth = Gauge(
            "trialsentinel_audit_queue_depth", "Audits queued or running", registry=r
        )
        self.llm_calls = Counter("trialsentinel_llm_calls", "LLM calls made by audits", registry=r)
        self.llm_tokens = Counter(
            "trialsentinel_llm_tokens", "LLM tokens used by audits", ["direction"], registry=r
        )
        self.verifier_decisions = Counter(
            "trialsentinel_verifier_decisions",
            "Claim decisions per verifier layer (deterministic categories, judge verdicts)",
            ["layer", "outcome"],
            registry=r,
        )
        self.claims = Counter(
            "trialsentinel_claims",
            "Claims in finished audits: verified (published) or rejected",
            ["decision"],
            registry=r,
        )

    def track_queue(self, depth: Callable[[], int]) -> None:
        self.queue_depth.set_function(depth)

    def observe_request(self, method: str, route: str, status: int, seconds: float) -> None:
        self.http_requests.labels(method, route, str(status)).inc()
        self.http_latency.labels(method, route).observe(seconds)

    def observe_audit(
        self, status: str, duration_s: float | None, result: dict[str, Any] | None
    ) -> None:
        self.audits.labels(status).inc()
        if duration_s is not None:
            self.audit_duration.observe(duration_s)
        if not result:
            return
        self.claims.labels("verified").inc(len(result.get("claims", [])))
        self.claims.labels("rejected").inc(len(result.get("rejected", [])))
        trace = result.get("trace") or {}
        self.llm_calls.inc(trace.get("llm_calls", 0))
        self.llm_tokens.labels("input").inc(trace.get("input_tokens", 0))
        self.llm_tokens.labels("output").inc(trace.get("output_tokens", 0))
        for layer, counts in (trace.get("decisions") or {}).items():
            for outcome, count in counts.items():
                self.verifier_decisions.labels(layer, outcome).inc(count)
