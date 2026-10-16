from datetime import date

import polars as pl
import pytest

from aim.boundaries.db.forecast_reader import ALL_ACCOUNTS_ID, join_actuals
from aim.kernel.ports import EVAL_SCHEMA

D1, D2 = date(2026, 10, 1), date(2026, 10, 2)


def _forecasts(rows):
    schema = {k: EVAL_SCHEMA[k] for k in ("article_id", "account_id", "date", "forecast")}
    return pl.DataFrame(rows, schema=schema, orient="row")


def _actuals(rows):
    schema = {k: EVAL_SCHEMA[k] for k in ("article_id", "account_id", "date", "actual")}
    return pl.DataFrame(rows, schema=schema, orient="row")


def _actual_of(frame, article, account, day):
    row = frame.filter(
        (pl.col("article_id") == article)
        & (pl.col("account_id") == account)
        & (pl.col("date") == day)
    )
    assert row.height == 1
    return row["actual"][0]


SALES = _actuals([(10, 7, D1, 3.0), (10, 8, D1, 5.0), (10, 7, D2, 2.0), (11, 7, D1, 40.0)])


def test_an_all_accounts_forecast_is_compared_with_the_sum_over_accounts():
    """Mutation: join on account_id without the all-accounts sum => actual 0.0 here, red."""
    frame = join_actuals(_forecasts([(10, ALL_ACCOUNTS_ID, D1, 9.0)]), SALES)
    assert _actual_of(frame, 10, ALL_ACCOUNTS_ID, D1) == pytest.approx(8.0)


def test_a_per_account_forecast_is_compared_with_that_account_only():
    frame = join_actuals(_forecasts([(10, 8, D1, 9.0)]), SALES)
    assert _actual_of(frame, 10, 8, D1) == pytest.approx(5.0)


def test_a_day_without_sales_is_actual_zero_and_other_articles_do_not_leak():
    frame = join_actuals(_forecasts([(10, ALL_ACCOUNTS_ID, date(2026, 10, 3), 1.0)]), SALES)
    assert _actual_of(frame, 10, ALL_ACCOUNTS_ID, date(2026, 10, 3)) == 0.0
    assert frame.schema == pl.Schema(EVAL_SCHEMA)
    assert frame.height == 1
