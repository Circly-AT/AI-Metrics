"""Forecast vs. actual for one tenant over a window of complete days.

Forecasts: one POINT value (`quantile IS NULL`) per (article, account, day), chosen by
the job's `ForecastSelection` -- one query per strategy in `SELECTIONS` (ADR 0004). Tenant
via `ai-jobs.cid`, since `ai-results` carries none. Backtests write no product rows, so
only forecast runs show up here.

Actuals: `sales` summed per (article, account, day), restricted to `type = 'sales'`.
A forecast day without a sale counts as actual 0.

Accounts: AI-Pipeline-V3 forecasts per article over ALL accounts and writes the placeholder
`accountId = 1` (`aip.boundaries.db.prediction_rows.ACCOUNT_ID`, a dummy row seeded in every
product DB). A forecast on that id is compared with the article's sales summed over all
accounts; a forecast on any other id with that account's sales (`join_actuals`).

OPEN (see README): day granularity and UTC day boundaries are assumptions -- the pipeline
converts orderDate into the tenant's time zone before it aggregates, and filters returns /
special sales per config. Until that is settled the numbers here can drift from the
pipeline's backtest metrics.
"""

from datetime import timedelta

import polars as pl
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aim.kernel.jobs import ForecastSelection, Window
from aim.kernel.ports import EVAL_SCHEMA

# latest_before_day: the latest ai-results row written before midnight at the start of the
# target day (`r.created_at < DATE(pv.date)`); ties on created_at go to the higher id.
LATEST_BEFORE_DAY = """SELECT articleId, accountId, day, forecast FROM (
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

SELECTIONS: dict[ForecastSelection, str] = {
    ForecastSelection.latest_before_day: LATEST_BEFORE_DAY,
}
"""Every strategy's query. Each returns (articleId, accountId, day, forecast) with at most
one row per (articleId, accountId, day), and takes :cid, :start, :end_exclusive."""

ACTUALS = """SELECT s.articleId, s.accountId, DATE(s.orderDate) AS day,
       CAST(SUM(s.quantity) AS DOUBLE) AS actual
  FROM sales s
 WHERE s.cid = :cid AND s.deleted_at IS NULL AND s.type = 'sales'
   AND s.orderDate >= :start AND s.orderDate < :end_exclusive
 GROUP BY s.articleId, s.accountId, DATE(s.orderDate)"""

_KEYS = ["article_id", "account_id", "date"]

ALL_ACCOUNTS_ID = 1
"""The pipeline's placeholder account for an all-accounts forecast."""


class DbForecastReader:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load(
        self, *, cid: str, window: Window, selection: ForecastSelection
    ) -> pl.DataFrame:
        params = {
            "cid": cid,
            "start": window.start,
            "end_exclusive": window.end + timedelta(days=1),
        }
        with self._engine.connect() as conn:
            forecasts = _frame(
                conn.execute(text(SELECTIONS[selection]), params).all(), "forecast"
            )
            if forecasts.is_empty():
                return pl.DataFrame(schema=EVAL_SCHEMA)
            actuals = _frame(conn.execute(text(ACTUALS), params).all(), "actual")
        return join_actuals(forecasts, actuals)


def join_actuals(forecasts: pl.DataFrame, actuals: pl.DataFrame) -> pl.DataFrame:
    """Forecast rows with their actual (0.0 without sales), shaped as EVAL_SCHEMA.
    `actuals` is per (article, account, day); for ALL_ACCOUNTS_ID forecasts it is summed
    over every account first."""
    all_accounts = (
        actuals.group_by("article_id", "date")
        .agg(pl.col("actual").sum())
        .with_columns(pl.lit(ALL_ACCOUNTS_ID, dtype=pl.Int64).alias("account_id"))
    )
    matched = pl.concat(
        [actuals.filter(pl.col("account_id") != ALL_ACCOUNTS_ID), all_accounts],
        how="diagonal",
    )
    return (
        forecasts.join(matched, on=_KEYS, how="left")
        .with_columns(pl.col("actual").fill_null(0.0))
        .select(list(EVAL_SCHEMA))
    )


def _frame(rows, value_column: str) -> pl.DataFrame:
    schema = {k: EVAL_SCHEMA[k] for k in (*_KEYS, value_column)}
    return pl.DataFrame([tuple(r) for r in rows], schema=schema, orient="row")
