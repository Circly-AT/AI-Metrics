"""The three seams the service needs. DB implementations live in `aim.boundaries.db`,
in-memory fakes in the tests."""

from collections.abc import Sequence
from typing import Literal, Protocol

import polars as pl

from aim.kernel.jobs import MetricJob, MetricJobConfig
from aim.kernel.results import MetricRow

EVAL_SCHEMA = {
    "article_id": pl.Int64,
    "account_id": pl.Int64,
    "date": pl.Date,
    "forecast": pl.Float64,
    "actual": pl.Float64,
}
"""What `ForecastReader.load` returns: one row per forecast point, actual already joined
(0.0 when there was no sale that day)."""

FailState = Literal["error", "error_management"]


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
    def load(self, *, cid: str, ai_job_id: int) -> pl.DataFrame:
        """Forecast vs. actual for the run, shaped as EVAL_SCHEMA."""
        ...


class MetricWriter(Protocol):
    def write(
        self, job: MetricJob, config: MetricJobConfig, rows: Sequence[MetricRow]
    ) -> None:
        """Replaces any earlier rows of this data-job (a re-triggered job writes once)."""
        ...
