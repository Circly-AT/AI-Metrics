import re

import pymysql
import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from aim.boundaries.db import grants
from aim.boundaries.db.engine import is_instance_fault, is_permanent_fault
from aim.boundaries.db.grants import CANONICAL_FORM, REQUIRED, Probe, check_grants


def _db_error(errno: int, cls=OperationalError):
    driver = (pymysql.err.ProgrammingError if cls is ProgrammingError else
              pymysql.err.OperationalError)(errno, "server says no")
    return cls("STATEMENT", {}, driver)


class FakeConn:
    def __init__(self, answers):
        self.answers = answers
        self.executed: list[str] = []

    def execute(self, clause):
        sql = str(clause)
        self.executed.append(sql)
        if sql in self.answers:
            raise self.answers[sql]

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeEngine:
    def __init__(self, answers=None):
        self.conn = FakeConn(answers or {})

    def connect(self):
        return self.conn


def test_every_probe_is_harmless_by_construction():
    """Mutation: a probe without `WHERE 1=0`, or a comment hiding it, would run for real."""
    for probe in REQUIRED:
        assert probe.statement == CANONICAL_FORM[probe.right].format(
            table=probe.table, column=probe.column
        )
        assert probe.statement.endswith(" WHERE 1=0")
        assert "--" not in probe.statement and "#" not in probe.statement


def test_every_table_the_sql_names_has_a_probe():
    """New table in a statement without a probe => the preflight would wave it through."""
    from aim.boundaries.db import forecast_reader, job_store, metric_writer

    named = set()
    for module in (forecast_reader, job_store, metric_writer):
        for value in vars(module).values():
            if isinstance(value, str) and re.search(r"\b(SELECT|UPDATE|INSERT|DELETE)\b", value):
                named |= set(re.findall(r"(?:FROM|JOIN|UPDATE|INTO)\s+`?([\w-]+)`?", value))
    named.discard("DUAL")
    assert named == {p.table for p in REQUIRED}


def test_all_gaps_are_reported_together_with_grant_statements():
    update_state = Probe("data-jobs", "UPDATE", "state").statement
    update_error = Probe("data-jobs", "UPDATE", "error").statement
    results = Probe("ai-metric-results", "SELECT").statement
    engine = FakeEngine({
        update_state: _db_error(1142),
        update_error: _db_error(1143),
        results: _db_error(1146, ProgrammingError),
    })
    report = check_grants(engine)

    assert not report.ok
    assert len(engine.conn.executed) == len(REQUIRED)  # did not stop at the first gap
    assert report.missing_tables == ("ai-metric-results",)
    text = report.describe(user="okremser", schema="circly_data_handling")
    assert "GRANT UPDATE (`state`, `error`) ON `circly_data_handling`.`data-jobs`" in text
    assert "`ai-metric-results`" in text
    assert "GRANT SELECT ON `circly_data_handling`.`sales`" not in text


def test_an_error_after_the_privilege_check_means_the_right_is_there():
    insert = Probe("ai-metric-results", "INSERT", "dataJobId").statement
    assert check_grants(FakeEngine({insert: _db_error(1364)})).ok  # field without default


def test_a_lost_connection_is_not_read_as_a_grant_verdict():
    first = REQUIRED[0].statement
    with pytest.raises(OperationalError):
        check_grants(FakeEngine({first: _db_error(2013)}))


@pytest.mark.parametrize("errno", [1044, 1045, 1049, 1142, 1143, 1146])
def test_configuration_errors_are_permanent_not_instance_faults(errno):
    """The log you saw: 1142 was backed off and retried forever. Mutation: drop the
    PERMANENT check in is_instance_fault => red."""
    exc = _db_error(errno)
    assert is_permanent_fault(exc)
    assert not is_instance_fault(exc)


@pytest.mark.parametrize("errno", [2003, 2006, 2013])
def test_connection_errors_stay_instance_faults(errno):
    exc = _db_error(errno)
    assert is_instance_fault(exc)
    assert not is_permanent_fault(exc)


def test_grants_module_never_names_the_url():
    assert "db_url" not in open(grants.__file__).read()
