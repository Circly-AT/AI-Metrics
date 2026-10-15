import numpy as np
import polars as pl
import pytest

from aim.kernel.ports import EVAL_SCHEMA
from aim.kernel.results import Level
from aim.metrics import point
from aim.metrics.evaluate import evaluate

A = np.array([10.0, 0.0, 4.0])
F = np.array([7.0, 2.0, 5.0])


def test_wape_is_abs_error_over_abs_actual():
    assert point.wape(A, F) == pytest.approx(6 / 14)


def test_mae_and_rmse():
    assert point.mae(A, F) == pytest.approx(2.0)
    assert point.rmse(A, F) == pytest.approx(np.sqrt(14 / 3))


def test_bias_sign_says_over_or_under_forecast():
    assert point.bias(A, F) == pytest.approx(0 / 14)
    assert point.bias(A, F + 1) == pytest.approx(3 / 14)
    assert point.bias(A, F - 1) == pytest.approx(-3 / 14)


@pytest.mark.parametrize("metric", [point.wape, point.bias])
def test_volume_metrics_are_undefined_without_actuals(metric):
    assert metric(np.zeros(3), np.ones(3)) is None


@pytest.mark.parametrize("metric", [point.wape, point.mae, point.rmse, point.bias])
def test_every_metric_is_undefined_on_empty_input(metric):
    assert metric(np.array([]), np.array([])) is None


def test_evaluate_gives_one_row_per_article_and_metric_plus_pooled_total():
    frame = pl.DataFrame(
        {
            "article_id": [1, 1, 2],
            "account_id": [7, 7, 7],
            "date": pl.date_range(pl.date(2026, 10, 1), pl.date(2026, 10, 3), eager=True),
            "forecast": [7.0, 2.0, 5.0],
            "actual": [10.0, 0.0, 4.0],
        },
        schema=EVAL_SCHEMA,
    )
    rows = evaluate(frame, {"wape": point.wape})

    by_key = {(r.level, r.article_id): r for r in rows}
    assert by_key[(Level.article, 1)].value == pytest.approx(5 / 10)
    assert by_key[(Level.article, 1)].n_points == 2
    assert by_key[(Level.article, 2)].value == pytest.approx(1 / 4)
    # Pooled, not the mean of the article values (which would be 0.375).
    assert by_key[(Level.total, None)].value == pytest.approx(6 / 14)
    assert by_key[(Level.total, None)].n_points == 3
    assert len(rows) == 3
