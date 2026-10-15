"""The metric job: a `circly_data_handling.data-jobs` row with `type = 'metrics'`.

    trigger --claim--> job_detected --finish--> finished
                           |
                           +--metric computation failed-------------> error
                           +--bad jobConfiguration / owner vanished--> error_management
"""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

JOB_TYPE = "metrics"
"""`data-jobs.type` this service claims; `abc_xyz` rows belong to someone else."""


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


class MetricJobConfig(BaseModel):
    """Shape of `jobConfiguration` for a metrics job. ASSUMED, not yet agreed with the
    backend: `{"aiJobId": 123}`."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    ai_job_id: int = Field(alias="aiJobId", gt=0)
    """The forecast run (`ai-jobs.id`) whose predictions are evaluated."""


class ConfigError(Exception):
    """`jobConfiguration` is missing or does not match `MetricJobConfig`."""


def parse_config(job: MetricJob) -> MetricJobConfig:
    if not job.configuration:
        raise ConfigError(f"data-job {job.id}: jobConfiguration is empty")
    try:
        return MetricJobConfig.model_validate_json(job.configuration, strict=True)
    except ValidationError as exc:
        raise ConfigError(f"data-job {job.id}: invalid jobConfiguration: {exc}") from exc
