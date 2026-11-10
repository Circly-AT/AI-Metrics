from datetime import date

import pymysql
import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from aim.boundaries.db.metric_writer import (
    ATTEMPTS,
    DEACTIVATE_RUN,
    INSERT_RESULTS,
    INSERT_RUN,
    LIST_METRICS,
    METRIC_NAMES,
    DbMetricWriter,
    MissingMetricError,
    describe_metric_row_problems,
    metric_row_problems,
)
from aim.kernel.jobs import ForecastSelection, MetricJob, Selection, Window
from aim.kernel.results import Level, MetricRow
from aim.metrics import METRICS

JOB = MetricJob(id=42, cid="c1", configuration=None)
WINDOW = Window(date(2026, 9, 15), date(2026, 10, 14))
LATEST = Selection(ForecastSelection.latest_before_day)
SEEDED = [(name, f"id-{name}") for name in METRIC_NAMES.values()]


def _db_error(errno, cls=OperationalError):
    driver = (pymysql.err.IntegrityError if cls is IntegrityError else
              pymysql.err.OperationalError)(errno, "server says no")
    return cls("STATEMENT", {}, driver)


class FakeResult:
    def __init__(self, rows=(), lastrowid=None):
        self.rows, self.lastrowid = list(rows), lastrowid

    def all(self):
        return self.rows


class FakeConn:
    def __init__(self, engine):
        self.engine = engine

    def execute(self, clause, params=None):
        sql = str(clause)
        self.engine.log.append((sql, params))
        if self.engine.fail_on.get(sql):
            raise self.engine.fail_on[sql].pop(0)
        if sql == LIST_METRICS:
            return FakeResult(self.engine.metrics)
        if sql == INSERT_RUN:
            self.engine.next_id += 1
            return FakeResult(lastrowid=self.engine.next_id)
        return FakeResult()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        self.engine.transactions.append("rollback" if exc_type else "commit")
        return False


class FakeEngine:
    def __init__(self, metrics=SEEDED, fail_on=None):
        self.metrics = metrics
        self.fail_on = fail_on or {}
        self.log: list[tuple[str, object]] = []
        self.transactions: list[str] = []
        self.next_id = 100

    def begin(self):
        return FakeConn(self)

    connect = begin

    def statements(self, sql):
        return [params for s, params in self.log if s == sql]


def _rows(metrics=("wape", "mae")):
    rows = []
    for m in metrics:
        rows += [
            MetricRow(Level.article, 7, 1, m, 0.5, 30),
            MetricRow(Level.article, 8, 3, m, None, 12),
            MetricRow(Level.total, None, None, m, 0.4, 42),
        ]
    return rows


def test_metric_names_cover_every_metric_aim_computes():
    assert set(METRIC_NAMES) == set(METRICS)


def test_each_metric_becomes_a_run_with_the_total_and_its_article_results():
    engine = FakeEngine()
    DbMetricWriter(engine).write(JOB, WINDOW, LATEST, _rows())

    assert engine.transactions == ["commit"]  # every metric of the job in ONE transaction
    identity = {"cid": "c1", "selection": "latest_before_day",
                "window_start": WINDOW.start, "window_end": WINDOW.end}
    assert engine.statements(DEACTIVATE_RUN) == [
        identity | {"metric_id": "id-WAPE"}, identity | {"metric_id": "id-MAE"},
    ]
    assert engine.statements(INSERT_RUN) == [
        identity | {"metric_id": "id-WAPE", "data_job_id": 42, "value": 0.4, "n_points": 42},
        identity | {"metric_id": "id-MAE", "data_job_id": 42, "value": 0.4, "n_points": 42},
    ]
    wape_results, mae_results = engine.statements(INSERT_RESULTS)
    assert wape_results == [
        {"run_id": 101, "article_id": 7, "account_id": 1, "value": 0.5, "n_points": 30},
        {"run_id": 101, "article_id": 8, "account_id": 3, "value": None, "n_points": 12},
    ]
    assert {r["run_id"] for r in mae_results} == {102}


def test_the_old_run_is_deactivated_before_the_new_one_is_inserted():
    """The unique key admits one active run per identity: inserting first would collide
    with the run being replaced. Mutation: swap the two statements => red."""
    engine = FakeEngine()
    DbMetricWriter(engine).write(JOB, WINDOW, LATEST, _rows(("wape",)))
    order = [sql for sql, _ in engine.log if sql in (DEACTIVATE_RUN, INSERT_RUN, INSERT_RESULTS)]
    assert order == [DEACTIVATE_RUN, INSERT_RUN, INSERT_RESULTS]


def test_a_run_without_article_rows_still_records_its_total():
    engine = FakeEngine()
    DbMetricWriter(engine).write(JOB, WINDOW, LATEST, [MetricRow(Level.total, None, None,
                                                                 "wape", None, 0)])
    assert len(engine.statements(INSERT_RUN)) == 1
    assert engine.statements(INSERT_RESULTS) == []


@pytest.mark.parametrize("failure", [_db_error(1213), _db_error(1062, IntegrityError)])
def test_a_collision_with_a_parallel_run_retries_the_whole_transaction(failure):
    """A deadlock or duplicate key means a parallel data-job wrote the same identity; the
    retry deactivates its run and becomes the newer one."""
    engine = FakeEngine(fail_on={INSERT_RUN: [failure]})
    DbMetricWriter(engine).write(JOB, WINDOW, LATEST, _rows(("wape",)))
    assert engine.transactions == ["rollback", "commit"]
    assert len(engine.statements(DEACTIVATE_RUN)) == 2  # the retry starts from the top


def test_retries_give_up_after_the_last_attempt():
    engine = FakeEngine(fail_on={INSERT_RUN: [_db_error(1213) for _ in range(ATTEMPTS)]})
    with pytest.raises(OperationalError):
        DbMetricWriter(engine).write(JOB, WINDOW, LATEST, _rows(("wape",)))
    assert engine.transactions == ["rollback"] * ATTEMPTS


def test_other_db_errors_are_not_retried():
    engine = FakeEngine(fail_on={INSERT_RUN: [_db_error(1142)]})
    with pytest.raises(OperationalError):
        DbMetricWriter(engine).write(JOB, WINDOW, LATEST, _rows(("wape",)))
    assert engine.transactions == ["rollback"]


@pytest.mark.parametrize(
    "metrics",
    [SEEDED[:1], [*SEEDED, ("MAE", "id-MAE-2")]],
    ids=["only WAPE seeded", "MAE doubled"],
)
def test_a_missing_or_doubled_metrics_row_fails_before_any_write(metrics):
    engine = FakeEngine(metrics=metrics)
    with pytest.raises(MissingMetricError, match="MAE"):
        DbMetricWriter(engine).write(JOB, WINDOW, LATEST, _rows())
    assert engine.statements(INSERT_RUN) == []


def test_the_startup_check_names_every_problem_with_its_remedy():
    problems = metric_row_problems(FakeEngine(metrics=[*SEEDED[:2], ("MAE", "x")]))
    assert problems == {"MAE": 2, "RMSE": 0, "R_BIAS": 0}
    text = describe_metric_row_problems(problems)
    assert "'MAE' has 2 rows; keep one" in text
    assert "SELECT UUID(), 'RMSE', 1" in text and "SELECT UUID(), 'R_BIAS', 1" in text
    assert metric_row_problems(FakeEngine()) == {}


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([MetricRow(Level.total, None, None, "mase", 1.0, 3)], "no metrics.name"),
        ([MetricRow(Level.article, 7, 1, "wape", 1.0, 3)], "0 total rows"),
        ([MetricRow(Level.total, None, None, "wape", 1.0, 3)] * 2, "2 total rows"),
        ([MetricRow(Level.article, None, 1, "wape", 1.0, 3)], "without article"),
    ],
)
def test_rows_that_do_not_form_runs_are_refused_before_the_db(rows, message):
    engine = FakeEngine()
    with pytest.raises(ValueError, match=message):
        DbMetricWriter(engine).write(JOB, WINDOW, LATEST, rows)
    assert engine.log == []

