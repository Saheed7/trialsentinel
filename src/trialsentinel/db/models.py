import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
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
    raw_uri: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
