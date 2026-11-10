"""The three seams the service needs. DB implementations live in `aim.boundaries.db`,
in-memory fakes in the tests."""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal, Protocol

import polars as pl

from aim.kernel.jobs import MetricJob, Selection, Window
from aim.kernel.results import MetricRow

EVAL_SCHEMA = {
    "article_id": pl.Int64,
    "account_id": pl.Int64,
    "date": pl.Date,
    "forecast": pl.Float64,
    "actual": pl.Float64,
}
"""What `ForecastReader.load` returns: one row per forecast point (already reduced to the
forecast that counts for that day), actual joined (0.0 when there was no sale that day)."""

FailState = Literal["error", "error_management"]


class TenantTimezoneError(Exception):
    """The tenant has no live `companies` row, or no usable time zone. A broken master
    record: the job fails (`error`) with this message, it never falls back to UTC."""


class JobStore(Protocol):
    def list_claimable(self, *, limit: int) -> Sequence[MetricJob]: ...

    def claim(self, job_id: int, *, owner: str) -> bool:
        """True exactly when THIS caller moved the row trigger -> job_detected."""
        ...

    def finish(self, job_id: int, *, owner: str) -> bool: ...

    def fail(self, job_id: int, *, owner: str, state: FailState, message: str) -> bool: ...

    def sweep_stale(self, *, grace_seconds: int) -> int:
        """Ends claimed rows not touched for `grace_seconds` as `error_management` (their
        owner stopped). Returns how many rows it ended."""
        ...


class ForecastReader(Protocol):
    """Days are the tenant's LOCAL calendar days (ADR 0008); the time zone stays behind
    this port. Both methods raise `TenantTimezoneError` for a broken tenant record."""

    def local_today(self, *, cid: str, now: datetime) -> date:
        """The tenant's calendar day at the aware instant `now`."""
        ...

    def ai_job_days(self, *, cid: str, ai_job_id: int) -> Window | None:
        """The tenant-local days ai-job `ai_job_id` holds point forecasts for, first to
        last; None when it holds none (or is not the tenant's, or is soft-deleted)."""
        ...

    def load(
        self, *, cid: str, window: Window, selection: Selection
    ) -> pl.DataFrame:
        """Forecast vs. actual of the tenant over the window, shaped as EVAL_SCHEMA. Per
        (article, account, day) exactly one forecast, chosen by `selection`."""
        ...


class MetricWriter(Protocol):
    def write(
        self,
        job: MetricJob,
        window: Window,
        selection: Selection,
        rows: Sequence[MetricRow],
    ) -> None:
        """Replaces any earlier rows of this data-job (a re-triggered job writes once)."""
        ...
