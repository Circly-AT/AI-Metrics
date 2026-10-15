from collections.abc import Mapping

import polars as pl

from aim.kernel.results import Level, MetricRow
from aim.metrics import METRICS, Metric


def evaluate(frame: pl.DataFrame, metrics: Mapping[str, Metric] = METRICS) -> list[MetricRow]:
    """Every metric per (article, account) and once pooled over the whole run.
    `frame` follows `aim.kernel.ports.EVAL_SCHEMA`."""
    rows: list[MetricRow] = []
    groups = frame.sort("article_id", "account_id", "date").group_by(
        "article_id", "account_id", maintain_order=True
    )
    for (article_id, account_id), group in groups:
        rows.extend(_rows(group, Level.article, article_id, account_id, metrics))
    rows.extend(_rows(frame, Level.total, None, None, metrics))
    return rows


def _rows(
    frame: pl.DataFrame,
    level: Level,
    article_id: int | None,
    account_id: int | None,
    metrics: Mapping[str, Metric],
) -> list[MetricRow]:
    actual = frame["actual"].to_numpy()
    forecast = frame["forecast"].to_numpy()
    return [
        MetricRow(
            level=level,
            article_id=article_id,
            account_id=account_id,
            metric=name,
            value=compute(actual, forecast),
            n_points=frame.height,
        )
        for name, compute in metrics.items()
    ]
