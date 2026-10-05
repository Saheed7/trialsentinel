"""Ingestion run lineage as a reusable async context manager."""

import time
import uuid
from collections import Counter
from types import TracebackType
from typing import Any, Self

from sqlalchemy import func, update

from trialsentinel.core.logging import get_logger
from trialsentinel.db.models import IngestionRun
from trialsentinel.db.session import get_sessionmaker

log = get_logger(__name__)


class RunTracker:
    """Records a run as 'running' on entry and its outcome and counters on exit."""

    def __init__(self, source: str, params: dict[str, Any]) -> None:
        self.source = source
        self.params = params
        self.run_id = uuid.uuid4()
        self.counts: Counter[str] = Counter()
        self.raw_uri: str | None = None
        self.status = "running"
        self._started = 0.0

    @property
    def duration_s(self) -> float:
        return round(time.perf_counter() - self._started, 2)

    async def __aenter__(self) -> Self:
        self._started = time.perf_counter()
        async with get_sessionmaker()() as session:
            session.add(
                IngestionRun(
                    id=self.run_id, source=self.source, params=self.params, status="running"
                )
            )
            await session.commit()
        log.info("run_started", run_id=str(self.run_id), source=self.source)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.status = "failed" if exc_type else "succeeded"
        async with get_sessionmaker()() as session:
            await session.execute(
                update(IngestionRun)
                .where(IngestionRun.id == self.run_id)
                .values(
                    status=self.status,
                    finished_at=func.now(),
                    fetched=self.counts["fetched"],
                    inserted=self.counts["inserted"],
                    updated=self.counts["updated"],
                    unchanged=self.counts["unchanged"],
                    invalid=self.counts["invalid"],
                    stats=dict(self.counts),
                    raw_uri=self.raw_uri,
                    error=exc_type.__name__ if exc_type else None,
                )
            )
            await session.commit()
        log.info(
            "run_finished",
            run_id=str(self.run_id),
            source=self.source,
            status=self.status,
            duration_s=self.duration_s,
        )
