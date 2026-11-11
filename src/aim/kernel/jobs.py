"""The metric job: a `circly_data_handling.data-jobs` row with `type = 'metrics'`.

    trigger --claim--> job_detected --finish--> finished
                           |
                           +--metric computation failed-------------> error
                           +--bad jobConfiguration / owner vanished--> error_management

A job evaluates ONE tenant (`cid`) over a window of complete days. Which of the several
forecasts for a day counts is the job's `ForecastSelection` (docs/decisions/0004).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

JOB_TYPE = "metrics"
"""`data-jobs.type` this service claims; `abc_xyz` rows belong to someone else."""

DEFAULT_WINDOW_DAYS = 30


class ForecastSelection(StrEnum):
    """Which forecast counts for a day when several runs forecast it (ADR 0004). A new
    strategy = a member here + its query in `aim.boundaries.db.forecast_reader.SELECTIONS`
    (a unit test holds the two together)."""

    latest_before_day = "latest_before_day"
    """The latest `ai-results` row written before the day began."""
    ai_job = "ai_job"
    """The results of ONE `ai-jobs` row, named by `jobConfiguration.aiJobId`, whenever
    they were written. The job must belong to the data-job's tenant. Should the job hold
    several results for an (article, account), the latest `created_at` counts, ties to
    the higher id."""


@dataclass(frozen=True)
class Selection:
    """A strategy with its parameters, as the reader and the writer need it."""

    strategy: ForecastSelection
    ai_job_id: int | None = None
    """Set exactly for `ForecastSelection.ai_job`."""

    def __post_init__(self) -> None:
        if (self.strategy is ForecastSelection.ai_job) != (self.ai_job_id is not None):
            raise ValueError(f"{self.strategy}: aiJobId {self.ai_job_id!r} does not fit")

    @property
    def params(self) -> dict[str, object]:
        """The strategy's own query parameters, bound next to :cid, :start, ..."""
        return {} if self.ai_job_id is None else {"ai_job_id": self.ai_job_id}

    @property
    def label(self) -> str:
        """What `ai-metric-runs.forecastSelection` records: the strategy, plus the
        ai-job for `ai_job` (`ai_job:123`) -- two ai-jobs evaluated over the same window
        are two run identities, never one replacing the other."""
        if self.ai_job_id is None:
            return str(self.strategy)
        return f"{self.strategy}:{self.ai_job_id}"

    def __str__(self) -> str:
        return self.label


class JobState(StrEnum):
    trigger = "trigger"
    job_detected = "job_detected"
    finished = "finished"
    error = "error"
    """The job itself failed: no forecasts, bad data, a metric raised."""
    error_management = "error_management"
    """The management side failed: unreadable jobConfiguration, claim abandoned."""


@dataclass(frozen=True)
class MetricJob:
    id: int
    cid: str
    configuration: str | None
    """`data-jobs.jobConfiguration` as stored -- parsed by `parse_config`."""


@dataclass(frozen=True)
class Window:
    """Inclusive range of complete days."""

    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def complete_before(self, today: date) -> Window | None:
        """The part of this window before `today` (whose sales are still arriving); None
        when no day of it is complete yet."""
        end = min(self.end, today - timedelta(days=1))
        return Window(self.start, end) if end >= self.start else None


class MetricJobConfig(BaseModel):
    """`jobConfiguration` of a metrics job. Every field is optional; an empty
    jobConfiguration means all defaults: the last 30 complete days, ending yesterday.

        {}                                            last 30 days
        {"windowDays": 90}                            last 90 days
        {"start": "2026-09-01", "end": "2026-09-30"}  fixed window
        {"forecastSelection": "latest_before_day"}    the default, spelled out
        {"forecastSelection": "ai_job", "aiJobId": 4711}   the results of ai-job 4711
                                                     over the days it forecast
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    window_days: int = Field(default=DEFAULT_WINDOW_DAYS, alias="windowDays", ge=1, le=366)
    start: date | None = None
    end: date | None = None
    forecast_selection: ForecastSelection = Field(
        default=ForecastSelection.latest_before_day, alias="forecastSelection"
    )
    ai_job_id: int | None = Field(default=None, alias="aiJobId", ge=1)

    @model_validator(mode="after")
    def _one_way_to_say_the_window(self) -> Self:
        if (self.start is None) != (self.end is None):
            raise ValueError("start and end go together")
        if self.start is not None:
            if "window_days" in self.model_fields_set:
                raise ValueError("give either windowDays or start/end, not both")
            if self.start > self.end:
                raise ValueError("start is after end")
        if (self.forecast_selection is ForecastSelection.ai_job) != (
            self.ai_job_id is not None
        ):
            raise ValueError("aiJobId goes with forecastSelection ai_job, and only with it")
        return self

    @property
    def has_explicit_window(self) -> bool:
        """`windowDays` or `start`/`end` given. Without either, `ai_job` evaluates the
        days its ai-job forecast; every other strategy the last `DEFAULT_WINDOW_DAYS`."""
        return self.start is not None or "window_days" in self.model_fields_set

    @property
    def selection(self) -> Selection:
        return Selection(self.forecast_selection, self.ai_job_id)

    def window(self, today: date) -> Window:
        """The days to evaluate. Never includes `today`: its sales are still arriving."""
        if self.start is not None:
            if self.end >= today:
                raise ConfigError(f"end {self.end} is not a complete day yet (today {today})")
            return Window(self.start, self.end)
        end = today - timedelta(days=1)
        return Window(end - timedelta(days=self.window_days - 1), end)


class ConfigError(Exception):
    """`jobConfiguration` does not match `MetricJobConfig`."""


def parse_config(job: MetricJob) -> MetricJobConfig:
    raw = (job.configuration or "").strip()
    if not raw:
        return MetricJobConfig()
    try:
        return MetricJobConfig.model_validate_json(raw, strict=True)
    except ValidationError as exc:
        raise ConfigError(f"data-job {job.id}: invalid jobConfiguration: {exc}") from exc
