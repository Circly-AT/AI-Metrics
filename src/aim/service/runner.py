import logging
from datetime import datetime

from aim.kernel.jobs import MetricJob, parse_config
from aim.kernel.ports import ForecastReader, MetricWriter
from aim.metrics.evaluate import evaluate

_LOGGER = logging.getLogger(__name__)


class NoForecastError(Exception):
    """No forecast covers the window -- a job fault, not an instance fault."""


class NoSalesError(Exception):
    """Forecasts but not a single sale in the window. A day without sales counts as actual
    0, so the run would compare every forecast with 0: WAPE and bias undefined, MAE and
    RMSE only the size of the forecasts. Seen when a tenant's sales import stops -- a job
    fault, so no misleading run is written."""


def run_job(
    job: MetricJob, *, reader: ForecastReader, writer: MetricWriter, now: datetime
) -> int:
    """Parse config -> load -> evaluate -> write. Returns the number of metric rows written.
    Raises `ConfigError` when jobConfiguration is unusable: before any DB read for its
    shape, after the tenant's "today" for a fixed window reaching into it. "Today" is the
    tenant's local day at `now` (ADR 0008). Raises `NoForecastError` / `NoSalesError`
    before anything is written when the window has no forecast or no sale."""
    config = parse_config(job)
    window = config.window(reader.local_today(cid=job.cid, now=now))
    selection = config.selection
    frame = reader.load(cid=job.cid, window=window, selection=selection)
    if frame.is_empty():
        raise NoForecastError(
            f"cid {job.cid}: no point forecast for {window.start}..{window.end} "
            f"under {selection}"
        )
    if not (frame["actual"] != 0).any():
        raise NoSalesError(
            f"cid {job.cid}: no sales in {window.start}..{window.end} "
            f"({frame.height} forecast points, all actuals 0) -- sales import missing?"
        )
    rows = evaluate(frame)
    writer.write(job, window, selection, rows)
    _LOGGER.info(
        "data-job %d (cid %s, %s..%s): %d points, %d metric rows",
        job.id,
        job.cid,
        window.start,
        window.end,
        frame.height,
        len(rows),
    )
    return len(rows)
