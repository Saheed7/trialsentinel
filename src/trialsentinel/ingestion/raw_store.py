"""Immutable raw archive of every payload fetched (local now; S3 in production)."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self, TextIO


class RawBatchWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.count = 0
        self._fh: TextIO | None = None

    @property
    def uri(self) -> str:
        return self.path.resolve().as_uri()

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = gzip.open(self.path, "wt", encoding="utf-8")  # noqa: SIM115
        return self

    def write(self, record: dict[str, Any]) -> None:
        if self._fh is None:
            raise RuntimeError("RawBatchWriter used outside its context manager")
        self._fh.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")
        self.count += 1

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._fh is not None:
            self._fh.close()


class LocalRawStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def open_batch(self, source: str, run_id: str) -> RawBatchWriter:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        return RawBatchWriter(self.root / source / f"dt={day}" / f"{run_id}.jsonl.gz")
