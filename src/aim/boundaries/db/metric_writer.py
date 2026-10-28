"""`ai-metric-runs` + `ai-metric-results` writes (ADR 0009, DDL: ddl/ai-metric-runs.sql).

One RUN per metric: the run carries the identity (cid, metric, forecast selection, window)
and the pooled total; its `ai-metric-results` rows carry the per-(article, account)
values. A re-run soft-deletes the active run of the same identity and inserts a new one;
the old run and its results stay as history. Readers take results of ACTIVE runs only
(`ai-metric-runs.deleted_at IS NULL`); result rows are never soft-deleted themselves.

All metrics of a data-job are written in ONE transaction. The unique key over the active
runs (`is_active`) makes "one active run per identity" a database guarantee; when two
data-jobs for the same identity race, InnoDB answers one of them with a deadlock or a
duplicate key, and running that transaction again makes it the newer run.
"""

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from aim.boundaries.db.driver_errors import DEADLOCK, DUPLICATE_KEY, errno_of
from aim.kernel.jobs import ForecastSelection, MetricJob, Window
from aim.kernel.results import Level, MetricRow

_LOGGER = logging.getLogger(__name__)

METRIC_NAMES: Mapping[str, str] = {"wape": "WAPE", "mae": "MAE", "rmse": "RMSE", "bias": "R_BIAS"}
"""AIM's metric -> `metrics.name`, the backend's master list (`ai-metric-runs.metricId`).
A unit test holds this to `aim.metrics.METRICS`. AIM's bias is relative,
`sum(f - a) / sum(|a|)`, hence `R_BIAS`; the backend's `A_BIAS` is the absolute one."""

LIST_METRICS = "SELECT name, id FROM metrics"

DEACTIVATE_RUN = """UPDATE `ai-metric-runs`
   SET deleted_at = UTC_TIMESTAMP(6)
 WHERE cid = :cid AND metricId = :metric_id AND forecastSelection = :selection
   AND windowStart = :window_start AND windowEnd = :window_end AND deleted_at IS NULL"""

INSERT_RUN = """INSERT INTO `ai-metric-runs`
  (dataJobId, cid, metricId, forecastSelection, windowStart, windowEnd, value, nPoints)
VALUES
  (:data_job_id, :cid, :metric_id, :selection, :window_start, :window_end, :value, :n_points)"""

INSERT_RESULTS = """INSERT INTO `ai-metric-results`
  (runId, articleId, accountId, value, nPoints)
VALUES
  (:run_id, :article_id, :account_id, :value, :n_points)"""

_RETRYABLE = frozenset({DEADLOCK, DUPLICATE_KEY})
ATTEMPTS = 3


class MissingMetricError(Exception):
    """A metric AIM computes has no `metrics` row (or more than one) to reference."""


@dataclass(frozen=True)
class _Run:
    metric: str
    total: MetricRow
    articles: tuple[MetricRow, ...]


class DbMetricWriter:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def write(
        self,
        job: MetricJob,
        window: Window,
        selection: ForecastSelection,
        rows: Sequence[MetricRow],
    ) -> None:
        runs = _runs(rows)
        for attempt in range(1, ATTEMPTS + 1):
            try:
                with self._engine.begin() as conn:
                    _write(conn, job, window, selection, runs)
                return
            except DBAPIError as exc:
                if errno_of(exc) not in _RETRYABLE or attempt == ATTEMPTS:
                    raise
                _LOGGER.warning(
                    "data-job %d: write collided with a parallel run (%s), attempt %d/%d",
                    job.id, errno_of(exc), attempt, ATTEMPTS,
                )


def _write(
    conn: Connection,
    job: MetricJob,
    window: Window,
    selection: ForecastSelection,
    runs: Sequence[_Run],
) -> None:
    metric_ids = _metric_ids(conn)
    for run in runs:
        identity = {
            "cid": job.cid,
            "metric_id": metric_ids[run.metric],
            "selection": selection,
            "window_start": window.start,
            "window_end": window.end,
        }
        conn.execute(text(DEACTIVATE_RUN), identity)
        run_id = conn.execute(
            text(INSERT_RUN),
            identity | {
                "data_job_id": job.id,
                "value": run.total.value,
                "n_points": run.total.n_points,
            },
        ).lastrowid
        if run.articles:
            conn.execute(
                text(INSERT_RESULTS),
                [
                    {
                        "run_id": run_id,
                        "article_id": row.article_id,
                        "account_id": row.account_id,
                        "value": row.value,
                        "n_points": row.n_points,
                    }
                    for row in run.articles
                ],
            )


def _metric_ids(conn: Connection) -> dict[str, str]:
    """AIM metric -> `metrics.id`. Exact names: a missing or doubled row is an error, not
    a guess (`metric_row_problems` reports the same at start)."""
    by_name: dict[str, list[str]] = defaultdict(list)
    for name, metric_id in conn.execute(text(LIST_METRICS)).all():
        by_name[name].append(metric_id)
    wrong = [n for n in METRIC_NAMES.values() if len(by_name.get(n, ())) != 1]
    if wrong:
        raise MissingMetricError(
            "metrics needs exactly one row per name, wrong for: " + ", ".join(wrong)
        )
    return {metric: by_name[name][0] for metric, name in METRIC_NAMES.items()}


def _runs(rows: Sequence[MetricRow]) -> tuple[_Run, ...]:
    """Groups `evaluate`'s rows into one run per metric: exactly one `total` row (the
    run's value) plus its `article` rows."""
    totals: dict[str, list[MetricRow]] = defaultdict(list)
    articles: dict[str, list[MetricRow]] = defaultdict(list)
    for row in rows:
        if row.metric not in METRIC_NAMES:
            raise ValueError(f"metric {row.metric!r} has no metrics.name in METRIC_NAMES")
        if row.level is Level.total:
            totals[row.metric].append(row)
        elif row.article_id is None or row.account_id is None:
            raise ValueError(f"article row without article/account: {row}")
        else:
            articles[row.metric].append(row)
    runs = []
    for metric in [*totals, *(m for m in articles if m not in totals)]:
        if len(totals[metric]) != 1:
            raise ValueError(f"metric {metric!r}: {len(totals[metric])} total rows, need 1")
        runs.append(_Run(metric, totals[metric][0], tuple(articles[metric])))
    return tuple(runs)


def metric_row_problems(engine: Engine) -> dict[str, int]:
    """`metrics.name` -> row count, for every name AIM writes that does not have exactly
    one row. Checked at start, like the grants, so no job is computed only to fail at
    the write."""
    with engine.connect() as conn:
        counts: dict[str, int] = defaultdict(int)
        for name, _ in conn.execute(text(LIST_METRICS)).all():
            counts[name] += 1
    return {n: counts[n] for n in METRIC_NAMES.values() if counts[n] != 1}


def describe_metric_row_problems(problems: Mapping[str, int]) -> str:
    """The startup message: an INSERT per missing row (the DDL's seeding form), a note
    per doubled one -- which of two rows is meant is for the backend to say."""
    lines = ["metrics needs exactly one row per name AIM writes:"]
    for name, count in problems.items():
        if count == 0:
            lines.append(
                f"  INSERT INTO `metrics` (`id`, `name`, `version`) SELECT UUID(), '{name}', 1"
                f" FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM `metrics` WHERE `name` = '{name}');"
            )
        else:
            lines.append(f"  '{name}' has {count} rows; keep one")
    return "\n".join(lines)
