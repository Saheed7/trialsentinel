import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from trialsentinel.db.base import Base


class Trial(Base):
    __tablename__ = "trials"

    nct_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    brief_title: Mapped[str] = mapped_column(Text)
    official_title: Mapped[str | None] = mapped_column(Text)
    overall_status: Mapped[str | None] = mapped_column(String(64), index=True)
    study_type: Mapped[str | None] = mapped_column(String(64))
    phases: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    conditions: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    lead_sponsor_name: Mapped[str | None] = mapped_column(Text, index=True)
    lead_sponsor_class: Mapped[str | None] = mapped_column(String(32))
    enrollment_count: Mapped[int | None]

    start_date: Mapped[date | None]
    start_date_precision: Mapped[str | None] = mapped_column(String(8))
    primary_completion_date: Mapped[date | None] = mapped_column(index=True)
    primary_completion_date_precision: Mapped[str | None] = mapped_column(String(8))
    primary_completion_type: Mapped[str | None] = mapped_column(String(16))
    completion_date: Mapped[date | None]
    completion_date_precision: Mapped[str | None] = mapped_column(String(8))
    results_first_submit_date: Mapped[date | None]
    results_first_post_date: Mapped[date | None]
    last_update_post_date: Mapped[date | None]

    has_results: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    brief_summary: Mapped[str | None] = mapped_column(Text)
    raw_hash: Mapped[str] = mapped_column(String(64))
    first_ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TrialOutcome(Base):
    __tablename__ = "trial_outcomes"
    __table_args__ = (UniqueConstraint("nct_id", "kind", "position"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    nct_id: Mapped[str] = mapped_column(ForeignKey("trials.nct_id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    position: Mapped[int] = mapped_column(Integer)
    measure: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    time_frame: Mapped[str | None] = mapped_column(Text)


class TrialIntervention(Base):
    __tablename__ = "trial_interventions"
    __table_args__ = (UniqueConstraint("nct_id", "position"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    nct_id: Mapped[str] = mapped_column(ForeignKey("trials.nct_id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    type: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(Text, index=True)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    unchanged: Mapped[int] = mapped_column(Integer, default=0)
    invalid: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    raw_uri: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class Publication(Base):
    __tablename__ = "publications"

    pmid: Mapped[str] = mapped_column(String(16), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    journal: Mapped[str | None] = mapped_column(Text)
    pub_date: Mapped[date | None]
    pub_date_precision: Mapped[str | None] = mapped_column(String(8))
    doi: Mapped[str | None] = mapped_column(Text)
    publication_types: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    registry_ids: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list)
    raw_hash: Mapped[str] = mapped_column(String(64))
    first_ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TrialPublicationLink(Base):
    """One row per (trial, paper, evidence type): the same pair can be linked several ways."""

    __tablename__ = "trial_publication_links"

    nct_id: Mapped[str] = mapped_column(
        ForeignKey("trials.nct_id", ondelete="CASCADE"), primary_key=True
    )
    pmid: Mapped[str] = mapped_column(String(16), primary_key=True, index=True)
    link_source: Mapped[str] = mapped_column(String(32), primary_key=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FaersDrug(Base):
    """A normalised drug term and how (or whether) it resolved in FAERS."""

    __tablename__ = "faers_drugs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    query_term: Mapped[str] = mapped_column(Text, unique=True)
    match_field: Mapped[str | None] = mapped_column(String(32))  # None = unresolved
    total_reports: Mapped[int] = mapped_column(BigInteger, default=0)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TrialDrugLink(Base):
    __tablename__ = "trial_drug_links"

    nct_id: Mapped[str] = mapped_column(
        ForeignKey("trials.nct_id", ondelete="CASCADE"), primary_key=True
    )
    drug_id: Mapped[int] = mapped_column(
        ForeignKey("faers_drugs.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    intervention_name: Mapped[str] = mapped_column(Text)


class FaersDrugEventStat(Base):
    """Disproportionality for one drug-event pair at one FAERS data snapshot."""

    __tablename__ = "faers_drug_event_stats"

    drug_id: Mapped[int] = mapped_column(
        ForeignKey("faers_drugs.id", ondelete="CASCADE"), primary_key=True
    )
    reaction_pt: Mapped[str] = mapped_column(Text, primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(primary_key=True)
    a: Mapped[int] = mapped_column(BigInteger)
    n_drug: Mapped[int] = mapped_column(BigInteger)
    n_event: Mapped[int] = mapped_column(BigInteger)
    n_total: Mapped[int] = mapped_column(BigInteger)
    prr: Mapped[float | None] = mapped_column(Float)
    ror: Mapped[float | None] = mapped_column(Float)
    ror_ci_low: Mapped[float | None] = mapped_column(Float)
    ror_ci_high: Mapped[float | None] = mapped_column(Float)
    chi2: Mapped[float | None] = mapped_column(Float)
    is_signal: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
