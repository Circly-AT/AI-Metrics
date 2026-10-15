"""Forecast vs. actual for one forecast run.

Forecasts: the POINT rows (`quantile IS NULL`) of `prediction_values`, reached through
`ai-results.aiJobId`. Actuals: `sales` summed per (article, account, day), restricted to
`type = 'sales'`. A forecast day without a sale counts as actual 0.

OPEN (see README): day granularity and UTC day boundaries are assumptions -- the pipeline
converts orderDate into the tenant's time zone before it aggregates, and filters returns /
special sales per config. Until that is settled the numbers here can drift from the
pipeline's backtest metrics.
"""

import polars as pl
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aim.kernel.ports import EVAL_SCHEMA

FORECASTS = """SELECT r.articleId, r.accountId, DATE(pv.date) AS day,
       CAST(SUM(pv.value) AS DOUBLE) AS forecast
  FROM prediction_values pv
  JOIN `ai-results` r ON r.id = pv.aiResultId
 WHERE r.aiJobId = :ai_job_id AND r.deleted_at IS NULL AND pv.deleted_at IS NULL
   AND pv.quantile IS NULL AND r.articleId IS NOT NULL
 GROUP BY r.articleId, r.accountId, DATE(pv.date)"""

ACTUALS = """SELECT s.articleId, s.accountId, DATE(s.orderDate) AS day,
       CAST(SUM(s.quantity) AS DOUBLE) AS actual
  FROM sales s
 WHERE s.cid = :cid AND s.deleted_at IS NULL AND s.type = 'sales'
   AND s.orderDate >= :start AND s.orderDate < :end + INTERVAL 1 DAY
   AND s.articleId IN (SELECT DISTINCT r.articleId FROM `ai-results` r
                        WHERE r.aiJobId = :ai_job_id AND r.deleted_at IS NULL)
 GROUP BY s.articleId, s.accountId, DATE(s.orderDate)"""

_KEYS = ["article_id", "account_id", "date"]


class DbForecastReader:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load(self, *, cid: str, ai_job_id: int) -> pl.DataFrame:
        with self._engine.connect() as conn:
            forecasts = _frame(
                conn.execute(text(FORECASTS), {"ai_job_id": ai_job_id}).all(), "forecast"
            )
            if forecasts.is_empty():
                return pl.DataFrame(schema=EVAL_SCHEMA)
            actuals = _frame(
                conn.execute(
                    text(ACTUALS),
                    {
                        "cid": cid,
                        "ai_job_id": ai_job_id,
                        "start": forecasts["date"].min(),
                        "end": forecasts["date"].max(),
                    },
                ).all(),
                "actual",
            )
        return (
            forecasts.join(actuals, on=_KEYS, how="left")
            .with_columns(pl.col("actual").fill_null(0.0))
            .select(list(EVAL_SCHEMA))
        )


def _frame(rows, value_column: str) -> pl.DataFrame:
    schema = {k: EVAL_SCHEMA[k] for k in (*_KEYS, value_column)}
    return pl.DataFrame([tuple(r) for r in rows], schema=schema, orient="row")
