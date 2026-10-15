"""`ai-metric-results` writes (DDL proposal: ddl/ai-metric-results.sql). Delete + insert in
ONE transaction, so a re-triggered data-job never leaves rows of two runs behind."""

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aim.kernel.jobs import MetricJob, MetricJobConfig
from aim.kernel.results import MetricRow

DELETE_FOR_JOB = "DELETE FROM `ai-metric-results` WHERE dataJobId = :data_job_id"

INSERT = """INSERT INTO `ai-metric-results`
  (dataJobId, aiJobId, cid, level, articleId, accountId, metric, value, nPoints)
VALUES
  (:data_job_id, :ai_job_id, :cid, :level, :article_id, :account_id, :metric, :value,
   :n_points)"""


class DbMetricWriter:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def write(
        self, job: MetricJob, config: MetricJobConfig, rows: Sequence[MetricRow]
    ) -> None:
        params = [
            {
                "data_job_id": job.id,
                "ai_job_id": config.ai_job_id,
                "cid": job.cid,
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
