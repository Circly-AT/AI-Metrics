"""`ai-metric-results` writes (DDL proposal: ddl/ai-metric-results.sql). Delete + insert in
ONE transaction, so a re-triggered data-job never leaves rows of two runs behind."""

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aim.kernel.jobs import MetricJob, Window
from aim.kernel.results import MetricRow

DELETE_FOR_JOB = "DELETE FROM `ai-metric-results` WHERE dataJobId = :data_job_id"

INSERT = """INSERT INTO `ai-metric-results`
  (dataJobId, cid, windowStart, windowEnd, level, articleId, accountId, metric, value, nPoints)
VALUES
  (:data_job_id, :cid, :window_start, :window_end, :level, :article_id, :account_id,
   :metric, :value, :n_points)"""


class DbMetricWriter:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def write(
        self, job: MetricJob, window: Window, rows: Sequence[MetricRow]
    ) -> None:
        params = [
            {
                "data_job_id": job.id,
                "cid": job.cid,
                "window_start": window.start,
                "window_end": window.end,
                "level": row.level,
                "article_id": row.article_id,
                "account_id": row.account_id,
                "metric": row.metric,
                "value": row.value,
                "n_points": row.n_points,
            }
            for row in rows
        ]
        with self._engine.begin() as conn:
            conn.execute(text(DELETE_FOR_JOB), {"data_job_id": job.id})
            if params:
                conn.execute(text(INSERT), params)
