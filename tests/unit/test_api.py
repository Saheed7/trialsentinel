from concurrent.futures import Executor, Future
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from trialsentinel.agents.service import AuditOptions
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
