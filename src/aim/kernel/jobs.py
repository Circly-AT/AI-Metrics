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


class MetricJobConfig(BaseModel):
    """`jobConfiguration` of a metrics job. Every field is optional; an empty
    jobConfiguration means all defaults: the last 30 complete days, ending yesterday.

        {}                                            last 30 days
        {"windowDays": 90}                            last 90 days
        {"start": "2026-09-01", "end": "2026-09-30"}  fixed window
        {"forecastSelection": "latest_before_day"}    the default, spelled out
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    window_days: int = Field(default=DEFAULT_WINDOW_DAYS, alias="windowDays", ge=1, le=366)
    start: date | None = None
    end: date | None = None
    forecast_selection: ForecastSelection = Field(
        default=ForecastSelection.latest_before_day, alias="forecastSelection"
    )

    @model_validator(mode="after")
    def _one_way_to_say_the_window(self) -> Self:
        if (self.start is None) != (self.end is None):
            raise ValueError("start and end go together")
        if self.start is not None:
            if "window_days" in self.model_fields_set:
                raise ValueError("give either windowDays or start/end, not both")
            if self.start > self.end:
                raise ValueError("start is after end")
        return self

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
