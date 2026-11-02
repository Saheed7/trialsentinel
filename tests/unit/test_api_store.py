from datetime import UTC, date, datetime

from sqlalchemy.dialects import postgresql

from trialsentinel.api.jobs import AuditJob
from trialsentinel.api.store import _to_job, audit_upsert
from trialsentinel.db.models import AgentAudit


def _job(**changes: object) -> AuditJob:
    job = AuditJob(
        audit_id="c" * 32,
        nct_id="NCT03317028",
        as_of=date(2026, 10, 5),
        submitted_at=datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
        options={"prompt_version": 2},
        revision=2,
        status="succeeded",
        result={"verifier_version": 3, "claims": []},
    )
    for name, value in changes.items():
        setattr(job, name, value)
    return job


def test_upsert_only_overwrites_with_a_newer_revision() -> None:
    sql = str(audit_upsert(_job()).compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (id) DO UPDATE" in sql
    assert "WHERE agent_audits.revision < excluded.revision" in sql


def test_upsert_records_the_verifier_version_from_the_result() -> None:
    params = audit_upsert(_job()).compile(dialect=postgresql.dialect()).params
    assert params["verifier_version"] == 3
    assert params["status"] == "succeeded"


def test_row_round_trips_to_a_job() -> None:
    row = AgentAudit(
        id=__import__("uuid").UUID(hex="c" * 32),
        nct_id="NCT03317028",
        as_of_date=date(2026, 10, 5),
        status="failed",
        options={},
        key_fingerprint="abcd1234",
        result=None,
        error="interrupted",
        revision=3,
        submitted_at=datetime(2026, 11, 2, tzinfo=UTC),
        started_at=None,
        finished_at=None,
    )
    job = _to_job(row)
    assert job.audit_id == "c" * 32
    assert (job.status, job.error, job.revision) == ("failed", "interrupted", 3)
