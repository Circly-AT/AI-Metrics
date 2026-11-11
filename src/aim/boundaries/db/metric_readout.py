"""Read-only queries for monitoring (`aim-mcp`), behind `aim.kernel.ports.MetricReadout`.

Reads exactly what docs/contracts/ai-metric-results.md promises readers: current numbers
are ACTIVE runs (`deleted_at IS NULL`), compared within one `forecastSelection`. Every
statement is a SELECT; the grants it needs are `READOUT_REQUIRED` in `grants.py`.

The session is pinned to UTC (engine.py), so the naive datetimes MariaDB hands back are UTC.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from aim.boundaries.db.forecast_reader import CUSTOMERS_DB
from aim.boundaries.db.metric_writer import METRIC_NAMES
from aim.kernel.jobs import JOB_TYPE, JobState
from aim.kernel.readout import CustomerActivity, JobRef, RunRow

_METRIC_KEYS = {name: key for key, name in METRIC_NAMES.items()}
"""`metrics.name` -> AIM's key (`R_BIAS` -> `bias`)."""

_JOB_COLUMNS = "id, cid, state, containerID, error, created_at, updated_at"

JOB_COUNTS = """SELECT state, COUNT(*) FROM `data-jobs`
 WHERE type = :type AND deleted_at IS NULL AND updated_at >= :since
 GROUP BY state"""

BACKLOG = """SELECT COUNT(*), MIN(created_at) FROM `data-jobs`
 WHERE type = :type AND state = :trigger AND deleted_at IS NULL"""

CLAIMED = f"""SELECT {_JOB_COLUMNS} FROM `data-jobs`
 WHERE type = :type AND state = :detected AND deleted_at IS NULL
 ORDER BY updated_at ASC, id ASC"""

LAST_FINISHED = """SELECT MAX(updated_at) FROM `data-jobs`
 WHERE type = :type AND state = :finished AND deleted_at IS NULL"""

RECENT_FAILURES = f"""SELECT {_JOB_COLUMNS} FROM `data-jobs`
 WHERE type = :type AND state IN (:error, :error_management) AND deleted_at IS NULL
   AND updated_at >= :since
 ORDER BY updated_at DESC, id DESC
 LIMIT :limit"""

CUSTOMER_ACTIVITY = """SELECT cid, MAX(created_at), MAX(windowEnd) FROM `ai-metric-runs`
 WHERE deleted_at IS NULL
 GROUP BY cid"""

# One row per (tenant, metric): its latest active run of the selection. "Latest" = the
# latest window end, then the latest run -- a re-run of an older window must not hide a
# newer one. `{cid_filter}` is one of two fixed strings, never user text.
LATEST_RUNS = """SELECT r.cid, m.name, r.forecastSelection, r.windowStart, r.windowEnd,
       r.value, r.nPoints, r.dataJobId, r.created_at
  FROM (SELECT r.*, ROW_NUMBER() OVER (
                 PARTITION BY r.cid, r.metricId
                 ORDER BY r.windowEnd DESC, r.created_at DESC, r.id DESC) AS rn
          FROM `ai-metric-runs` r
         WHERE r.deleted_at IS NULL AND r.forecastSelection = :selection
           AND r.created_at >= :since{cid_filter}) r
  JOIN `metrics` m ON m.id = r.metricId
 WHERE r.rn = 1
 ORDER BY r.cid, m.name"""

# Its own query, not a JOIN: `ai-metric-runs.cid` and `companies.id` live in two schemas
# whose collations are not guaranteed to match.
COMPANY_NAMES = f"""SELECT id, companyName FROM `{CUSTOMERS_DB}`.`companies`
 WHERE id IN :cids AND deleted_at IS NULL"""


def _utc(value: datetime | None) -> datetime | None:
    return None if value is None else value.replace(tzinfo=UTC)


def _job(row) -> JobRef:
    return JobRef(
        id=int(row[0]), cid=row[1], state=row[2], owner=row[3], error=row[4],
        created_at=_utc(row[5]), updated_at=_utc(row[6]),
    )


class DbMetricReadout:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def _all(self, statement, params: Mapping[str, object]):
        with self._engine.connect() as conn:
            clause = text(statement) if isinstance(statement, str) else statement
            return conn.execute(clause, params).all()

    def job_counts(self, *, since: datetime) -> Mapping[str, int]:
        rows = self._all(JOB_COUNTS, {"type": JOB_TYPE, "since": since})
        return {str(state): int(n) for state, n in rows}

    def backlog(self) -> tuple[int, datetime | None]:
        (n, oldest), = self._all(BACKLOG, {"type": JOB_TYPE, "trigger": JobState.trigger})
        return int(n), _utc(oldest)

    def claimed(self) -> Sequence[JobRef]:
        rows = self._all(CLAIMED, {"type": JOB_TYPE, "detected": JobState.job_detected})
        return tuple(_job(r) for r in rows)

    def last_finished_at(self) -> datetime | None:
        (value,), = self._all(LAST_FINISHED, {"type": JOB_TYPE, "finished": JobState.finished})
        return _utc(value)

    def recent_failures(self, *, since: datetime, limit: int) -> Sequence[JobRef]:
        params = {
            "type": JOB_TYPE, "error": JobState.error,
            "error_management": JobState.error_management, "since": since, "limit": limit,
        }
        return tuple(_job(r) for r in self._all(RECENT_FAILURES, params))

    def customer_activity(self) -> Sequence[CustomerActivity]:
        return tuple(
            CustomerActivity(cid=r[0], last_run_at=_utc(r[1]), last_window_end=r[2])
            for r in self._all(CUSTOMER_ACTIVITY, {})
        )

    def latest_runs(
        self, *, selection: str, since: datetime, cid: str | None
    ) -> Sequence[RunRow]:
        sql = LATEST_RUNS.format(cid_filter=" AND r.cid = :cid" if cid else "")
        params: dict[str, object] = {"selection": selection, "since": since}
        if cid:
            params["cid"] = cid
        return tuple(
            RunRow(
                cid=r[0], metric=_METRIC_KEYS.get(r[1], str(r[1]).lower()), selection=r[2],
                window_start=r[3], window_end=r[4],
                value=None if r[5] is None else float(r[5]), n_points=int(r[6]),
                data_job_id=int(r[7]), created_at=_utc(r[8]),
            )
            for r in self._all(sql, params)
        )

    def company_names(self, cids: Sequence[str]) -> Mapping[str, str]:
        if not cids:
            return {}
        statement = text(COMPANY_NAMES).bindparams(bindparam("cids", expanding=True))
        rows = self._all(statement, {"cids": list(cids)})
        return {r[0]: r[1] for r in rows if r[1]}
