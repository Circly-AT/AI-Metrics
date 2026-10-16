"""Startup preflight: may this DB user do everything AIM's statements will do?

Without it a missing grant shows up at the first statement that needs it -- for the
results table only after a whole job has been computed. Same approach as AI-Pipeline-V3's
`grant_preflight` (ADR 0038 there), simplified.

Every probe ends in `WHERE 1=0`: MariaDB checks privileges when it PREPARES the statement,
before the predicate is evaluated, so a probe proves the right without touching a row
(measured by the pipeline against MariaDB). The four forms are fixed (`CANONICAL_FORM`);
`tests/unit/test_grants.py` holds every probe to its form, because a hand-written probe
like "DELETE FROM `x` -- WHERE 1=0" would delete the table.

New statement on a new table/right/column => add its probe here in the same commit.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from aim.boundaries.db.driver_errors import (
    COLUMN_ACCESS_DENIED,
    NO_SUCH_TABLE,
    TABLE_ACCESS_DENIED,
    errno_of,
)

_CLIENT_ERRORS = 2000
"""2000+ are client-side errors (2003 can't connect, 2013 lost connection, ...)."""

CANONICAL_FORM = {
    "SELECT": "SELECT 1 FROM `{table}` WHERE 1=0",
    "INSERT": "INSERT INTO `{table}` (`{column}`) SELECT NULL FROM DUAL WHERE 1=0",
    # `= NULL`, not `= col`: reading the column would need SELECT too.
    "UPDATE": "UPDATE `{table}` SET `{column}` = NULL WHERE 1=0",
    "DELETE": "DELETE FROM `{table}` WHERE 1=0",
}


@dataclass(frozen=True)
class Probe:
    table: str
    right: str
    column: str = ""

    @property
    def statement(self) -> str:
        return CANONICAL_FORM[self.right].format(table=self.table, column=self.column)


REQUIRED: tuple[Probe, ...] = (
    # job_store.py
    Probe("data-jobs", "SELECT"),
    Probe("data-jobs", "UPDATE", "state"),
    Probe("data-jobs", "UPDATE", "containerID"),
    Probe("data-jobs", "UPDATE", "error"),
    # forecast_reader.py
    Probe("prediction_values", "SELECT"),
    Probe("ai-results", "SELECT"),
    Probe("ai-jobs", "SELECT"),
    Probe("sales", "SELECT"),
    # metric_writer.py -- the DELETE's WHERE reads dataJobId, hence SELECT as well
    Probe("ai-metric-results", "SELECT"),
    Probe("ai-metric-results", "INSERT", "dataJobId"),
    Probe("ai-metric-results", "DELETE"),
)


@dataclass(frozen=True)
class GrantReport:
    missing_rights: tuple[Probe, ...]
    missing_tables: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.missing_rights and not self.missing_tables

    def describe(self, *, user: str, schema: str) -> str:
        """Every gap at once (one restart per missing grant would be ping-pong), with the
        GRANT statements a DBA can run. Never contains the URL: it carries the password."""
        lines = [f"DB user '{user}' cannot do everything AIM needs in schema `{schema}`:"]
        if self.missing_tables:
            tables = ", ".join(f"`{t}`" for t in self.missing_tables)
            lines.append(f"  tables that do not exist: {tables} (see ddl/)")
        by_grant: dict[tuple[str, str], list[str]] = {}
        for p in self.missing_rights:
            by_grant.setdefault((p.table, p.right), []).append(p.column)
        for (table, right), columns in by_grant.items():
            cols = f" ({', '.join(f'`{c}`' for c in columns)})" if any(columns) else ""
            lines.append(f"  GRANT {right}{cols} ON `{schema}`.`{table}` TO '{user}'@'<host>';")
        return "\n".join(lines)


def check_grants(engine: Engine, probes: tuple[Probe, ...] = REQUIRED) -> GrantReport:
    """Runs every probe. 1142/1143 = right missing, 1146 = table missing, anything else
    (including success) = right present. A connect failure (wrong password, unknown
    schema, DB down) raises -- that is not a grant question."""
    missing_rights: list[Probe] = []
    missing_tables: list[str] = []
    with engine.connect() as conn:
        for probe in probes:
            try:
                conn.execute(text(probe.statement))
            except DBAPIError as exc:
                code = errno_of(exc)
                if code in (TABLE_ACCESS_DENIED, COLUMN_ACCESS_DENIED):
                    missing_rights.append(probe)
                elif code == NO_SUCH_TABLE:
                    if probe.table not in missing_tables:
                        missing_tables.append(probe.table)
                elif code is None or code >= _CLIENT_ERRORS:
                    raise  # connection trouble says nothing about grants
                # Any other server error: the privilege check passed, the statement
                # failed later (e.g. 1364 on the INSERT form) -- the right is there.
            finally:
                conn.rollback()
    return GrantReport(tuple(missing_rights), tuple(missing_tables))
