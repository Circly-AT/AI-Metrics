import logging

from aim.kernel.jobs import MetricJob, parse_config
from aim.kernel.ports import ForecastReader, MetricWriter
from aim.metrics.evaluate import evaluate

_LOGGER = logging.getLogger(__name__)


class NoForecastError(Exception):
    """The run has no point forecasts to evaluate -- a job fault, not an instance fault."""


def run_job(job: MetricJob, *, reader: ForecastReader, writer: MetricWriter) -> int:
    """Parse config -> load -> evaluate -> write. Returns the number of metric rows written.
    Raises `ConfigError` before touching the DB when jobConfiguration is unusable."""
    config = parse_config(job)
    frame = reader.load(cid=job.cid, ai_job_id=config.ai_job_id)
    if frame.is_empty():
        raise NoForecastError(f"ai-jobs id {config.ai_job_id} has no point forecasts")
    rows = evaluate(frame)
    writer.write(job, config, rows)
    _LOGGER.info(
        "data-job %d (ai-job %d, cid %s): %d points, %d metric rows",
        job.id, config.ai_job_id, job.cid, frame.height, len(rows),
    )
    return len(rows)
