"""The seams the service needs. DB implementations live in `aim.boundaries.db`,
in-memory fakes in the tests."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Literal, Protocol

import polars as pl

from aim.kernel.jobs import MetricJob, Selection, Window
from aim.kernel.readout import CustomerActivity, JobRef, RunRow
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


class MetricReadout(Protocol):
    """Read-only view for monitoring (`aim-mcp`). Never writes; datetimes are aware UTC."""

    def job_counts(self, *, since: datetime) -> Mapping[str, int]:
        """`data-jobs` (type=metrics) per state, among rows updated at or after `since`."""
        ...

    def backlog(self) -> tuple[int, datetime | None]:
        """Rows waiting in `trigger`, and the oldest one's `created_at`."""
        ...

    def claimed(self) -> Sequence[JobRef]:
        """Rows in `job_detected` (being computed, or abandoned), oldest first."""
        ...

    def last_finished_at(self) -> datetime | None: ...

    def recent_failures(self, *, since: datetime, limit: int) -> Sequence[JobRef]:
        """`error` / `error_management` rows updated at or after `since`, newest first."""
        ...

    def customer_activity(self) -> Sequence[CustomerActivity]: ...

    def latest_runs(
        self, *, selection: str, since: datetime, cid: str | None
    ) -> Sequence[RunRow]:
        """Per (tenant, metric) the latest ACTIVE run of `selection` created at or after
        `since`: the latest window end wins, then the latest run."""
        ...

    def company_names(self, cids: Sequence[str]) -> Mapping[str, str]:
        """`companies.companyName` per cid; tenants without a live row or name are absent."""
        ...
