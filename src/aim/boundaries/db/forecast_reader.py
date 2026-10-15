"""Forecast vs. actual for one tenant over a window of complete days.

Forecasts -- rule (a): for each (article, account, day) the POINT value (`quantile IS
NULL`) from the `ai-results` row with the latest `created_at` before that day began. Ties
on `created_at` go to the higher id. Tenant via `ai-jobs.cid`, since `ai-results` carries
none. Backtests write no product rows, so only forecast runs show up here.

Actuals: `sales` summed per (article, account, day), restricted to `type = 'sales'`.
A forecast day without a sale counts as actual 0.

OPEN (see README): day granularity and UTC day boundaries are assumptions -- the pipeline
converts orderDate into the tenant's time zone before it aggregates, and filters returns /
special sales per config. Until that is settled the numbers here can drift from the
pipeline's backtest metrics.
"""

from datetime import timedelta

import polars as pl
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aim.kernel.jobs import Window
from aim.kernel.ports import EVAL_SCHEMA

# `r.created_at < DATE(pv.date)`: written before midnight at the start of the target day.
FORECASTS = """SELECT articleId, accountId, day, forecast FROM (
  SELECT r.articleId, r.accountId, DATE(pv.date) AS day,
         CAST(pv.value AS DOUBLE) AS forecast,
         ROW_NUMBER() OVER (PARTITION BY r.articleId, r.accountId, DATE(pv.date)
                            ORDER BY r.created_at DESC, r.id DESC) AS rn
    FROM prediction_values pv
    JOIN `ai-results` r ON r.id = pv.aiResultId
    JOIN `ai-jobs` j ON j.id = r.aiJobId
   WHERE j.cid = :cid AND j.deleted_at IS NULL
     AND r.deleted_at IS NULL AND r.articleId IS NOT NULL
     AND pv.deleted_at IS NULL AND pv.quantile IS NULL
     AND pv.date >= :start AND pv.date < :end_exclusive
     AND r.created_at < DATE(pv.date)
) ranked
 WHERE rn = 1"""

ACTUALS = """SELECT s.articleId, s.accountId, DATE(s.orderDate) AS day,
       CAST(SUM(s.quantity) AS DOUBLE) AS actual
  FROM sales s
 WHERE s.cid = :cid AND s.deleted_at IS NULL AND s.type = 'sales'
   AND s.orderDate >= :start AND s.orderDate < :end_exclusive
 GROUP BY s.articleId, s.accountId, DATE(s.orderDate)"""

_KEYS = ["article_id", "account_id", "date"]


class DbForecastReader:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load(self, *, cid: str, window: Window) -> pl.DataFrame:
        params = {
            "cid": cid,
            "start": window.start,
            "end_exclusive": window.end + timedelta(days=1),
        }
        with self._engine.connect() as conn:
            forecasts = _frame(conn.execute(text(FORECASTS), params).all(), "forecast")
            if forecasts.is_empty():
                return pl.DataFrame(schema=EVAL_SCHEMA)
            actuals = _frame(conn.execute(text(ACTUALS), params).all(), "actual")
        return (
            forecasts.join(actuals, on=_KEYS, how="left")
            .with_columns(pl.col("actual").fill_null(0.0))
            .select(list(EVAL_SCHEMA))
        )


def _frame(rows, value_column: str) -> pl.DataFrame:
    schema = {k: EVAL_SCHEMA[k] for k in (*_KEYS, value_column)}
    return pl.DataFrame([tuple(r) for r in rows], schema=schema, orient="row")
