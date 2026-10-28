import re

import pymysql
import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from aim.boundaries.db import grants
from aim.boundaries.db.engine import is_instance_fault, is_permanent_fault
from aim.boundaries.db.grants import (
    CANONICAL_FORM,
    CURRENT_ACCOUNT,
    REQUIRED,
    Probe,
    check_grants,
)


def _db_error(errno: int, cls=OperationalError):
    driver = (pymysql.err.ProgrammingError if cls is ProgrammingError else
              pymysql.err.OperationalError)(errno, "server says no")
    return cls("STATEMENT", {}, driver)


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class FakeConn:
    def __init__(self, answers, account):
        self.answers = answers
        self.account = account
        self.executed: list[str] = []

    def execute(self, clause):
        sql = str(clause)
        if sql == CURRENT_ACCOUNT:
            return FakeResult(self.account)
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
    def __init__(self, answers=None, account="okremser@%"):
        self.conn = FakeConn(answers or {}, account)

    def connect(self):
        return self.conn


def test_every_probe_is_harmless_by_construction():
    """Mutation: a probe without `WHERE 1=0`, or a comment hiding it, would run for real."""
    for probe in REQUIRED:
        assert probe.statement == CANONICAL_FORM[probe.right].format(
            ref=probe.ref, column=probe.column
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
                named |= set(re.findall(
                    r"(?:FROM|JOIN|UPDATE|INTO)\s+(?:`?([\w-]+)`?\.)?`?([\w-]+)`?", value
                ))
    named.discard(("", "DUAL"))
    assert ("circly_customers", "timezones") in named  # the regex sees qualified names
    assert named == {(p.schema, p.table) for p in REQUIRED}


def _written(sql: str) -> set[tuple[str, str, str]]:
    """(table, right, column) for every column an INSERT names in its column list or an
    UPDATE assigns in its SET; empty for any other statement."""
    if match := re.match(r"\s*INSERT INTO\s+`?([\w-]+)`?\s*\(([^)]*)\)", sql):
        table, columns = match.group(1), match.group(2).split(",")
        return {(table, "INSERT", c.strip().strip("`")) for c in columns}
    if match := re.match(r"\s*UPDATE\s+`?([\w-]+)`?\s+SET\b(.*?)\bWHERE\b", sql, re.S):
        columns = re.findall(r"(?:^|,)\s*`?(\w+)`?\s*=", match.group(2))
        return {(match.group(1), "UPDATE", c) for c in columns}
    return set()


def test_every_written_column_has_a_probe():
    """Column grants are per column: probing only dataJobId waved through a grant that
    the real 11-column INSERT then failed with 1143. Mutation: drop a column probe, or add
    a column to a statement without one => red."""
    from aim.boundaries.db import forecast_reader, job_store, metric_writer

    written = set()
    for module in (forecast_reader, job_store, metric_writer):
        for value in vars(module).values():
            if isinstance(value, str):
                written |= _written(value)
    assert {t for t, _, _ in written} == {"data-jobs", "ai-metric-runs", "ai-metric-results"}
    probed = {(p.table, p.right, p.column) for p in REQUIRED if p.right in ("INSERT", "UPDATE")}
    assert written == probed


def test_all_gaps_are_reported_together_with_grant_statements():
    update_state = Probe("data-jobs", "UPDATE", "state").statement
    update_error = Probe("data-jobs", "UPDATE", "error").statement
    results = Probe("ai-metric-runs", "SELECT").statement
    engine = FakeEngine({
        update_state: _db_error(1142),
        update_error: _db_error(1143),
        results: _db_error(1146, ProgrammingError),
    })
    report = check_grants(engine)

    assert not report.ok
    assert len(engine.conn.executed) == len(REQUIRED)  # did not stop at the first gap
    assert report.missing_tables == ("`ai-metric-runs`",)
    text = report.describe(schema="circly_data_handling")
    assert ("GRANT UPDATE (`state`, `error`) ON `circly_data_handling`.`data-jobs`"
            " TO 'okremser'@'%';") in text
    assert "`ai-metric-runs`" in text
    assert "GRANT SELECT ON `circly_data_handling`.`sales`" not in text


def test_grants_name_the_account_the_server_matched_not_a_placeholder():
    """`'<host>'` in a GRANT fails or creates a new account; the server's CURRENT_USER()
    is the account that actually needs the right."""
    metrics = Probe("metrics", "SELECT").statement
    engine = FakeEngine({metrics: _db_error(1142)}, account="aim@10.0.%")
    text = check_grants(engine).describe(schema="s")
    assert "DB account 'aim'@'10.0.%' cannot" in text
    assert "GRANT SELECT ON `s`.`metrics` TO 'aim'@'10.0.%';" in text
    assert "<host>" not in text


def test_a_table_in_another_schema_is_granted_in_that_schema():
    zones = Probe("timezones", "SELECT", schema="circly_customers").statement
    assert zones == "SELECT 1 FROM `circly_customers`.`timezones` WHERE 1=0"
    text = check_grants(FakeEngine({zones: _db_error(1142)})).describe(schema="data")
    assert "GRANT SELECT ON `circly_customers`.`timezones` TO 'okremser'@'%';" in text


def test_an_error_after_the_privilege_check_means_the_right_is_there():
    insert = Probe("ai-metric-runs", "INSERT", "dataJobId").statement
    assert check_grants(FakeEngine({insert: _db_error(1364)})).ok  # field without default


def test_a_column_the_table_lacks_is_reported_not_waved_through():
    """dev's ai-metric-results had no `cid`: 1054 passed the preflight as "right present"
    and would have failed the first job's INSERT. Mutation: drop the 1054 branch => red."""
    cid = Probe("ai-metric-runs", "INSERT", "cid")
    report = check_grants(FakeEngine({cid.statement: _db_error(1054, ProgrammingError)}))
    assert not report.ok
    assert report.missing_columns == (cid,)
    assert "columns AIM writes that do not exist: `ai-metric-runs`.`cid`" in report.describe(
        schema="s"
    )


def test_a_lost_connection_is_not_read_as_a_grant_verdict():
    first = REQUIRED[0].statement
    with pytest.raises(OperationalError):
        check_grants(FakeEngine({first: _db_error(2013)}))


@pytest.mark.parametrize("errno", [1044, 1045, 1049, 1054, 1142, 1143, 1146])
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
