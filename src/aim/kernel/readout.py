"""Read side: what AIM has done and computed, for monitoring (`aim-mcp`).

Domain rows only; the queries live in `aim.boundaries.db.metric_readout`, the assembly into
tool answers in `aim.service.readout`. Every datetime here is timezone-aware UTC.
"""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class JobRef:
    """One `data-jobs` row of type `metrics`."""

    id: int
    cid: str
    state: str
    owner: str | None
    """`containerID`: `<hostname>:<pid>` of the claiming instance, if claimed."""
    error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class RunRow:
    """One ACTIVE `ai-metric-runs` row (`deleted_at IS NULL`)."""

    cid: str
    metric: str
    """AIM's metric key (`wape`, `mae`, `rmse`, `bias`); another `metrics.name`, lowercased."""
    selection: str
    """`ai-metric-runs.forecastSelection`: `latest_before_day` or `ai_job:<id>`."""
    window_start: date
    window_end: date
    value: float | None
    """Pooled over every point of the run; None = undefined, never 0."""
    n_points: int
    data_job_id: int
    created_at: datetime


@dataclass(frozen=True)
class CustomerActivity:
    """When AIM last wrote an active run for a tenant (any metric, any selection)."""

    cid: str
    last_run_at: datetime
    last_window_end: date
