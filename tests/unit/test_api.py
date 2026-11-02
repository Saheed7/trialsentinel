from concurrent.futures import Executor, Future
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from trialsentinel.agents.service import AuditOptions
from trialsentinel.api.jobs import AuditJob, AuditJobs
from trialsentinel.api.main import create_app
from trialsentinel.api.schemas import Assessment, Finding
from trialsentinel.api.security import ApiSettings, hash_key, new_key

KEY = "ts_test-key-not-a-secret"
AUTH = {"X-API-Key": KEY}
AS_OF = date(2026, 10, 5)
NCT = "NCT03317028"


class InlineExecutor(Executor):
    """Runs submitted work immediately, so tests need no threads or waiting."""

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Future:
        future: Future = Future()
        future.set_result(fn(*args, **kwargs))
        return future


class NeverExecutor(Executor):
    """Accepts work and never runs it: simulates a busy worker."""

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Future:
        return Future()


class FakeStore:
    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy
        self.audits = {}

    async def ping(self) -> None:
        if not self.healthy:
            raise ConnectionError("database down")

    async def findings(self, as_of: date) -> list[Finding]:
        return [
            Finding(
                nct_id=NCT,
                finding="overdue",
                reporting_status="overdue",
                days_overdue=1993,
                days_late=None,
            ),
            Finding(
                nct_id="NCT05363670",
                finding="reported_late",
                reporting_status="reported_late",
                days_overdue=None,
                days_late=466,
            ),
        ]

    async def assessment(self, nct_id: str, as_of: date) -> Assessment | None:
        if nct_id != NCT:
            return None
        return Assessment(
            nct_id=NCT,
            as_of=as_of,
            rule_version="1",
            applicability="likely_act",
            applicability_confidence="high",
            reporting_status="overdue",
            standard_deadline=date(2021, 4, 21),
            effective_deadline=date(2021, 4, 21),
            days_overdue=1993,
            days_late=None,
            finding="overdue",
            basis=["interventional"],
        )

    async def case(self, nct_id: str, as_of: date) -> Any:
        return SimpleNamespace(nct_id=nct_id) if nct_id == NCT else None

    # Durable audits, with the same newer-revision-wins rule as the SQL upsert.
    audits: dict[str, AuditJob]
    fail_saves: bool = False

    async def save_audit(self, job: AuditJob) -> None:
        if self.fail_saves:
            raise ConnectionError("database down")
        stored = self.audits.get(job.audit_id)
        if stored is None or stored.revision < job.revision:
            self.audits[job.audit_id] = replace(job)

    async def load_audit(self, audit_id: str) -> AuditJob | None:
        job = self.audits.get(audit_id)
        return replace(job) if job else None

    async def list_audits(self, nct_id: str, limit: int) -> list[AuditJob]:
        jobs = [j for j in self.audits.values() if j.nct_id == nct_id]
        return sorted(jobs, key=lambda j: j.submitted_at, reverse=True)[:limit]

    async def mark_interrupted(self) -> int:
        stuck = [j for j in self.audits.values() if j.status in ("queued", "running")]
        for job in stuck:
            job.status, job.error, job.revision = "failed", "interrupted", job.revision + 1
        return len(stuck)


def fake_runner(case: Any, options: AuditOptions) -> dict[str, Any]:
    return {
        "verdict": "overdue",
        "status": "verified",
        "claims": [{"text": "The trial has not posted its results.", "evidence_ids": ["E2"]}],
        "rejected": [{"claim": "Completed on 2020-04-21.", "reason": "rule B", "round": "0"}],
        "evidence": [{"id": "E2", "kind": "registry", "text": "results posted: no"}],
        "audit_rows": [{"round": "0", "claim": "x", "deterministic": "pass", "judge": "n/a"}],
        "revisions": 1,
        "seconds": 1.5,
        "verifier_version": 3,
        "prompt_version": options.prompt_version,
        "judge": options.judge,
    }


def failing_runner(case: Any, options: AuditOptions) -> dict[str, Any]:
    raise RuntimeError("model unavailable")


def make_client(
    *,
    store: Any = None,
    runner: Any = fake_runner,
    executor: Executor | None = None,
    keys: str = hash_key(KEY),
    max_queued: int = 20,
) -> TestClient:
    app = create_app(
        store=store or FakeStore(),
        runner=runner,
        executor=executor or InlineExecutor(),
        api_settings=ApiSettings(keys_sha256=keys, max_queued_audits=max_queued),
    )
    return TestClient(app)


def test_health_is_public_and_carries_a_request_id() -> None:
    with make_client() as client:
        response = client.get("/healthz", headers={"X-Request-ID": "abc123"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "abc123"


def test_readiness_reports_database_state() -> None:
    with make_client() as client:
        assert client.get("/readyz").status_code == 200
    with make_client(store=FakeStore(healthy=False)) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert "down" not in response.text  # internal error details stay out of responses


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "ts_wrong"}])
def test_protected_endpoints_reject_missing_or_wrong_keys(headers: dict[str, str]) -> None:
    with make_client() as client:
        response = client.get("/v1/findings", headers=headers)
    assert response.status_code == 401


def test_api_fails_closed_without_configured_keys() -> None:
    with make_client(keys="") as client:
        assert client.get("/v1/findings", headers=AUTH).status_code == 503


def test_findings_are_paginated() -> None:
    with make_client() as client:
        body = client.get("/v1/findings?limit=1&offset=1", headers=AUTH).json()
    assert body["total"] == 2
    assert [f["nct_id"] for f in body["items"]] == ["NCT05363670"]


def test_assessment_found_missing_and_invalid_id() -> None:
    with make_client() as client:
        ok = client.get(f"/v1/trials/{NCT}/assessment", headers=AUTH)
        missing = client.get("/v1/trials/NCT00000000/assessment", headers=AUTH)
        invalid = client.get("/v1/trials/not-an-id/assessment", headers=AUTH)
    assert ok.status_code == 200
    assert ok.json()["days_overdue"] == 1993
    assert missing.status_code == 404
    assert invalid.status_code == 422


def test_audit_lifecycle_returns_verified_claims() -> None:
    with make_client() as client:
        created = client.post("/v1/audits", json={"nct_id": NCT, "judge": True}, headers=AUTH)
        assert created.status_code == 202
        location = created.headers["Location"]
        body = client.get(location, headers=AUTH).json()
    assert body["status"] == "succeeded"
    assert body["result"]["claims"][0]["evidence_ids"] == ["E2"]
    assert body["result"]["rejected"][0]["reason"] == "rule B"
    assert body["result"]["judge"] is True


def test_failed_audit_is_reported_not_raised() -> None:
    with make_client(runner=failing_runner) as client:
        audit_id = client.post("/v1/audits", json={"nct_id": NCT}, headers=AUTH).json()["audit_id"]
        body = client.get(f"/v1/audits/{audit_id}", headers=AUTH).json()
    assert body["status"] == "failed"
    assert body["error"] == "RuntimeError: model unavailable"


def test_audit_for_unassessed_trial_is_404_and_bad_input_is_422() -> None:
    with make_client() as client:
        missing = client.post("/v1/audits", json={"nct_id": "NCT00000000"}, headers=AUTH)
        assert missing.status_code == 404
        assert client.post("/v1/audits", json={"nct_id": "x"}, headers=AUTH).status_code == 422
        bad_prompt = {"nct_id": NCT, "prompt_version": 3}
        assert client.post("/v1/audits", json=bad_prompt, headers=AUTH).status_code == 422


def test_queue_limit_returns_429_with_retry_after() -> None:
    with make_client(executor=NeverExecutor(), max_queued=1) as client:
        first = client.post("/v1/audits", json={"nct_id": NCT}, headers=AUTH)
        second = client.post("/v1/audits", json={"nct_id": NCT}, headers=AUTH)
    assert first.status_code == 202
    assert first.json()["status"] == "queued"
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "60"


def test_unknown_audit_id_is_404() -> None:
    with make_client() as client:
        assert client.get(f"/v1/audits/{'0' * 32}", headers=AUTH).status_code == 404


def test_generated_keys_are_unique_and_hash_stably() -> None:
    a, b = new_key(), new_key()
    assert a != b and a.startswith("ts_")
    assert hash_key(a) == hash_key(a) and len(hash_key(a)) == 64


# --- Persistence -------------------------------------------------------------------------


def test_job_changes_are_reported_in_order_with_rising_revisions() -> None:
    seen: list[tuple[str, int]] = []
    jobs = AuditJobs(
        fake_runner,
        executor=InlineExecutor(),
        on_change=lambda job: seen.append((job.status, job.revision)),
    )
    jobs.submit(SimpleNamespace(nct_id=NCT), AuditOptions(), as_of=AS_OF)
    assert seen == [("queued", 0), ("running", 1), ("succeeded", 2)]


def test_finished_audit_is_persisted_with_options_and_key_fingerprint() -> None:
    store = FakeStore()
    with make_client(store=store) as client:
        audit_id = client.post("/v1/audits", json={"nct_id": NCT}, headers=AUTH).json()["audit_id"]
        client.get("/healthz")  # let the event loop run the scheduled writes
    saved = store.audits[audit_id]
    assert saved.status == "succeeded"
    assert saved.revision == 2
    assert saved.options["prompt_version"] == 2
    assert saved.key_fingerprint == hash_key(KEY)[:8]


def test_audit_survives_a_restart() -> None:
    store = FakeStore()
    with make_client(store=store) as client:
        audit_id = client.post("/v1/audits", json={"nct_id": NCT}, headers=AUTH).json()["audit_id"]
        client.get("/healthz")
    with make_client(store=store) as restarted:  # a new process: empty memory, same database
        body = restarted.get(f"/v1/audits/{audit_id}", headers=AUTH).json()
    assert body["status"] == "succeeded"
    assert body["result"]["claims"][0]["evidence_ids"] == ["E2"]


def test_audits_left_running_by_a_crash_are_marked_failed_at_startup() -> None:
    store = FakeStore()
    stuck = AuditJob(
        audit_id="a" * 32,
        nct_id=NCT,
        as_of=AS_OF,
        submitted_at=datetime.now(UTC),
        status="running",
        revision=1,
    )
    store.audits[stuck.audit_id] = stuck
    with make_client(store=store) as client:
        body = client.get(f"/v1/audits/{stuck.audit_id}", headers=AUTH).json()
    assert body["status"] == "failed"
    assert body["error"] == "interrupted"


def test_older_snapshot_never_overwrites_a_newer_one() -> None:
    import asyncio

    store = FakeStore()
    base = AuditJob(audit_id="b" * 32, nct_id=NCT, as_of=AS_OF, submitted_at=datetime.now(UTC))
    asyncio.run(store.save_audit(replace(base, status="succeeded", revision=2)))
    asyncio.run(store.save_audit(replace(base, status="running", revision=1)))  # arrives late
    assert store.audits[base.audit_id].status == "succeeded"


def test_database_outage_does_not_break_audits() -> None:
    store = FakeStore()
    store.fail_saves = True
    with make_client(store=store) as client:
        audit_id = client.post("/v1/audits", json={"nct_id": NCT}, headers=AUTH).json()["audit_id"]
        body = client.get(f"/v1/audits/{audit_id}", headers=AUTH).json()
    assert body["status"] == "succeeded"  # served from memory; the failed write is logged


def test_trial_audit_history_is_newest_first() -> None:
    store = FakeStore()
    with make_client(store=store) as client:
        first = client.post("/v1/audits", json={"nct_id": NCT}, headers=AUTH).json()["audit_id"]
        second = client.post("/v1/audits", json={"nct_id": NCT}, headers=AUTH).json()["audit_id"]
        client.get("/healthz")
        history = client.get(f"/v1/trials/{NCT}/audits", headers=AUTH).json()
    assert [h["audit_id"] for h in history] == [second, first]
    assert history[0]["outcome"] == "verified"
    assert history[0]["verified_claims"] == 1
