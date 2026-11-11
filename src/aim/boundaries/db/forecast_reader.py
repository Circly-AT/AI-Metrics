"""Forecast vs. actual for one tenant over a window of complete days.

Forecasts: one POINT value (`quantile IS NULL`) per (article, account, day), chosen by
the job's `Selection` -- one query per strategy in `SELECTIONS` (ADR 0004). Tenant
via `ai-jobs.cid`, since `ai-results` carries none. Backtests write no product rows, so
only forecast runs show up here.

Actuals: `sales` summed per (article, account, day), restricted to `type = 'sales'`.
A forecast day without a sale counts as actual 0.

Accounts: AI-Pipeline-V3 forecasts per article over ALL accounts and writes the placeholder
`accountId = 1` (`aip.boundaries.db.prediction_rows.ACCOUNT_ID`, a dummy row seeded in every
product DB). A forecast on that id is compared with the article's sales summed over all
accounts; a forecast on any other id with that account's sales (`join_actuals`).

Days are the tenant's LOCAL calendar days (ADR 0008). Each query joins `{days}`: one row
per window day with the UTC range `[day_start, day_end)` it covers, computed in Python by
`local_calendar_day` -- the pipeline's zone database, never MariaDB's CONVERT_TZ. An
instant belongs to the day whose range holds it, so a forecast day stored as local
midnight (what the pipeline writes) and one stored as 00:00 UTC (seen for Vienna tenants)
land on the same day.

OPEN (see README): day granularity is an assumption, and the pipeline filters returns /
special sales per config. Until that is settled the numbers here can drift from the
pipeline's backtest metrics.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime

import polars as pl
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from aim.boundaries.db.local_calendar_day import (
    LocalDay,
    local_day_of,
    local_days,
    require_usable_timezone,
)
from aim.kernel.jobs import ForecastSelection, Selection, Window
from aim.kernel.ports import EVAL_SCHEMA, TenantTimezoneError

CUSTOMERS_DB = "circly_customers"
"""Schema of the tenant master data, on the same server as the data schema."""

# A live tenant whose zone row is missing or soft-deleted gives `name = NULL`;
# `pointed_at` tells the two apart in the message. Same query as AI-Pipeline-V3's.
TIMEZONE = f"""SELECT t.name, z.name AS pointed_at
  FROM `{CUSTOMERS_DB}`.`companies` c
  LEFT JOIN `{CUSTOMERS_DB}`.`timezones` t ON c.timezoneId = t.id AND t.deleted_at IS NULL
  LEFT JOIN `{CUSTOMERS_DB}`.`timezones` z ON c.timezoneId = z.id
 WHERE c.id = :cid AND c.deleted_at IS NULL
 LIMIT 1"""

# latest_before_day: the latest ai-results row written before the tenant's day began
# (`r.created_at < d.day_start`, local midnight in UTC); ties on created_at go to the
# higher id.
LATEST_BEFORE_DAY = """SELECT articleId, accountId, day, forecast FROM (
  SELECT r.articleId, r.accountId, d.day,
         CAST(pv.value AS DOUBLE) AS forecast,
         ROW_NUMBER() OVER (PARTITION BY r.articleId, r.accountId, d.day
                            ORDER BY r.created_at DESC, r.id DESC) AS rn
    FROM prediction_values pv
    JOIN ({days}) d ON pv.date >= d.day_start AND pv.date < d.day_end
    JOIN `ai-results` r ON r.id = pv.aiResultId
    JOIN `ai-jobs` j ON j.id = r.aiJobId
   WHERE j.cid = :cid AND j.deleted_at IS NULL
     AND r.deleted_at IS NULL AND r.articleId IS NOT NULL
     AND pv.deleted_at IS NULL AND pv.quantile IS NULL
     AND pv.date >= :start AND pv.date < :end_exclusive
     AND r.created_at < d.day_start
) ranked
 WHERE rn = 1"""

# ai_job: the results of ai-job :ai_job_id, if it is the tenant's; no cut-off on
# created_at. Several results of the job for one key: latest created_at, then higher id.
AI_JOB = """SELECT articleId, accountId, day, forecast FROM (
  SELECT r.articleId, r.accountId, d.day,
         CAST(pv.value AS DOUBLE) AS forecast,
         ROW_NUMBER() OVER (PARTITION BY r.articleId, r.accountId, d.day
                            ORDER BY r.created_at DESC, r.id DESC) AS rn
    FROM prediction_values pv
    JOIN ({days}) d ON pv.date >= d.day_start AND pv.date < d.day_end
    JOIN `ai-results` r ON r.id = pv.aiResultId
    JOIN `ai-jobs` j ON j.id = r.aiJobId
   WHERE j.id = :ai_job_id AND j.cid = :cid AND j.deleted_at IS NULL
     AND r.deleted_at IS NULL AND r.articleId IS NOT NULL
     AND pv.deleted_at IS NULL AND pv.quantile IS NULL
     AND pv.date >= :start AND pv.date < :end_exclusive
) ranked
 WHERE rn = 1"""

# The instants of ai-job :ai_job_id's first and last point forecast -- the same rows
# AI_JOB reads, without the window. Their local days are the job's default window.
AI_JOB_SPAN = """SELECT MIN(pv.date), MAX(pv.date)
  FROM prediction_values pv
  JOIN `ai-results` r ON r.id = pv.aiResultId
  JOIN `ai-jobs` j ON j.id = r.aiJobId
 WHERE j.id = :ai_job_id AND j.cid = :cid AND j.deleted_at IS NULL
   AND r.deleted_at IS NULL AND r.articleId IS NOT NULL
   AND pv.deleted_at IS NULL AND pv.quantile IS NULL"""

SELECTIONS: dict[ForecastSelection, str] = {
    ForecastSelection.latest_before_day: LATEST_BEFORE_DAY,
    ForecastSelection.ai_job: AI_JOB,
}
"""Every strategy's query. Each returns (articleId, accountId, day, forecast) with at most
one row per (articleId, accountId, day), and takes `{days}`, :cid, :start, :end_exclusive
(`:start`/`:end_exclusive` are the UTC bounds of the whole window, for the index) plus the
strategy's own `Selection.params`."""

ACTUALS = """SELECT s.articleId, s.accountId, d.day,
       CAST(SUM(s.quantity) AS DOUBLE) AS actual
  FROM sales s
  JOIN ({days}) d ON s.orderDate >= d.day_start AND s.orderDate < d.day_end
 WHERE s.cid = :cid AND s.deleted_at IS NULL AND s.type = 'sales'
   AND s.orderDate >= :start AND s.orderDate < :end_exclusive
 GROUP BY s.articleId, s.accountId, d.day"""

_KEYS = ["article_id", "account_id", "date"]

ALL_ACCOUNTS_ID = 1
"""The pipeline's placeholder account for an all-accounts forecast."""


class DbForecastReader:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def local_today(self, *, cid: str, now: datetime) -> date:
        with self._engine.connect() as conn:
            zone = tenant_timezone(conn, cid)
        return local_day_of(now, zone)

    def ai_job_days(self, *, cid: str, ai_job_id: int) -> Window | None:
        with self._engine.connect() as conn:
            first, last = conn.execute(
                text(AI_JOB_SPAN), {"cid": cid, "ai_job_id": ai_job_id}
            ).one()
            if first is None:
                return None
            zone = tenant_timezone(conn, cid)
        return Window(
            local_day_of(first.replace(tzinfo=UTC), zone),
            local_day_of(last.replace(tzinfo=UTC), zone),
        )

    def load(
        self, *, cid: str, window: Window, selection: Selection
    ) -> pl.DataFrame:
        with self._engine.connect() as conn:
            days = local_days(window, tenant_timezone(conn, cid))
            table, params = days_table(days)
            params |= {"cid": cid, "start": days[0].start, "end_exclusive": days[-1].end}
            query = SELECTIONS[selection.strategy].format(days=table)
            forecasts = _frame(
                conn.execute(text(query), params | selection.params).all(), "forecast"
            )
            if forecasts.is_empty():
                return pl.DataFrame(schema=EVAL_SCHEMA)
            actuals = _frame(
                conn.execute(text(ACTUALS.format(days=table)), params).all(), "actual"
            )
        return join_actuals(forecasts, actuals)


def tenant_timezone(conn: Connection, cid: str) -> str:
    row = conn.execute(text(TIMEZONE), {"cid": cid}).first()
    if row is None:
        raise TenantTimezoneError(f"no live {CUSTOMERS_DB}.companies row for cid {cid!r}")
    name, pointed_at = row
    if name is None:
        target = (
            f"the soft-deleted zone row {pointed_at!r}"
            if pointed_at is not None
            else "no timezones row at all"
        )
        raise TenantTimezoneError(
            f"cid {cid!r} has no live timezone: companies.timezoneId points at {target}"
        )
    return require_usable_timezone(name, cid=cid)


def days_table(days: Sequence[LocalDay]) -> tuple[str, dict[str, object]]:
    """The `{days}` derived table: one `SELECT` per day, values bound as parameters (never
    formatted into the SQL). The CASTs give the columns DATE / DATETIME(6) types instead
    of the strings the driver sends."""
    selects, params = [], {}
    for i, day in enumerate(days):
        selects.append(
            f"SELECT CAST(:day_{i} AS DATE) AS day, CAST(:day_start_{i} AS DATETIME(6))"
            f" AS day_start, CAST(:day_end_{i} AS DATETIME(6)) AS day_end"
        )
        params |= {f"day_{i}": day.day, f"day_start_{i}": day.start, f"day_end_{i}": day.end}
    return "\n UNION ALL ".join(selects), params


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
