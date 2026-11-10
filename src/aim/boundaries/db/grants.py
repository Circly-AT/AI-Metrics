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
    UNKNOWN_COLUMN,
    errno_of,
)
from aim.boundaries.db.forecast_reader import CUSTOMERS_DB

_CLIENT_ERRORS = 2000
"""2000+ are client-side errors (2003 can't connect, 2013 lost connection, ...)."""

CANONICAL_FORM = {
    "SELECT": "SELECT 1 FROM {ref} WHERE 1=0",
    "INSERT": "INSERT INTO {ref} (`{column}`) SELECT NULL FROM DUAL WHERE 1=0",
    # `= NULL`, not `= col`: reading the column would need SELECT too.
    "UPDATE": "UPDATE {ref} SET `{column}` = NULL WHERE 1=0",
    "DELETE": "DELETE FROM {ref} WHERE 1=0",
}


@dataclass(frozen=True)
class Probe:
    table: str
    right: str
    column: str = ""
    schema: str = ""
    """Empty: the connection's schema (`Settings.data_db`)."""

    @property
    def ref(self) -> str:
        return f"`{self.schema}`.`{self.table}`" if self.schema else f"`{self.table}`"

    @property
    def statement(self) -> str:
        return CANONICAL_FORM[self.right].format(ref=self.ref, column=self.column)


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
    Probe("companies", "SELECT", schema=CUSTOMERS_DB),
    Probe("timezones", "SELECT", schema=CUSTOMERS_DB),
    # metric_writer.py -- the deactivating UPDATE's WHERE reads the run's identity, hence
    # SELECT on ai-metric-runs. One INSERT probe per written column: a column-level grant
    # on some of them would pass a single probe and fail the real INSERT with 1143, after
    # a whole job was computed.
    Probe("metrics", "SELECT"),
    Probe("ai-metric-runs", "SELECT"),
    Probe("ai-metric-runs", "UPDATE", "deleted_at"),
    *(
        Probe("ai-metric-runs", "INSERT", column)
        for column in (
            "dataJobId", "cid", "metricId", "forecastSelection", "windowStart", "windowEnd",
            "value", "nPoints",
        )
    ),
    *(
        Probe("ai-metric-results", "INSERT", column)
        for column in ("runId", "articleId", "accountId", "value", "nPoints")
    ),
)

READOUT_REQUIRED: tuple[Probe, ...] = (
    # metric_readout.py -- the read-only monitoring server (`aim-mcp`). Deliberately its own
    # list: the server should run as a SELECT-only account, not with `aim serve`'s rights.
    Probe("data-jobs", "SELECT"),
    Probe("ai-metric-runs", "SELECT"),
    Probe("metrics", "SELECT"),
    Probe("companies", "SELECT", schema=CUSTOMERS_DB),
)

CURRENT_ACCOUNT = "SELECT CURRENT_USER()"
"""The account the server matched (`user@host-pattern`), which is what a GRANT must name --
not the username from the settings plus the host we happen to connect from."""


@dataclass(frozen=True)
class GrantReport:
    missing_rights: tuple[Probe, ...]
    missing_tables: tuple[str, ...]
    missing_columns: tuple[Probe, ...]
    account: str
    """Quoted for a GRANT, e.g. `'okremser'@'%'`."""

    @property
    def ok(self) -> bool:
        return not (self.missing_rights or self.missing_tables or self.missing_columns)

    def describe(self, *, schema: str) -> str:
        """Every gap at once (one restart per missing grant would be ping-pong), with the
        GRANT statements a DBA can run. Never contains the URL: it carries the password."""
        lines = [f"DB account {self.account} cannot do everything AIM needs in schema `{schema}`:"]
        if self.missing_tables:
            tables = ", ".join(self.missing_tables)
            lines.append(f"  tables that do not exist: {tables} (see ddl/)")
        if self.missing_columns:
            columns = ", ".join(f"{p.ref}.`{p.column}`" for p in self.missing_columns)
            lines.append(f"  columns AIM writes that do not exist: {columns} (see ddl/)")
        by_grant: dict[tuple[str, str], list[str]] = {}
        for p in self.missing_rights:
            ref = p.ref if p.schema else f"`{schema}`.{p.ref}"
            by_grant.setdefault((ref, p.right), []).append(p.column)
        for (ref, right), columns in by_grant.items():
            cols = f" ({', '.join(f'`{c}`' for c in columns)})" if any(columns) else ""
            lines.append(f"  GRANT {right}{cols} ON {ref} TO {self.account};")
        return "\n".join(lines)


def check_grants(engine: Engine, probes: tuple[Probe, ...] = REQUIRED) -> GrantReport:
    """Runs every probe. 1142/1143 = right missing, 1146 = table missing, 1054 = column
    missing (the table differs from ddl/), anything else (including success) = right
    present. A connect failure (wrong password, unknown schema, DB down) raises -- that is
    not a grant question."""
    missing_rights: list[Probe] = []
    missing_tables: list[str] = []
    missing_columns: list[Probe] = []
    with engine.connect() as conn:
        user, _, host = conn.execute(text(CURRENT_ACCOUNT)).scalar_one().rpartition("@")
        for probe in probes:
            try:
                conn.execute(text(probe.statement))
            except DBAPIError as exc:
                code = errno_of(exc)
                if code in (TABLE_ACCESS_DENIED, COLUMN_ACCESS_DENIED):
                    missing_rights.append(probe)
                elif code == NO_SUCH_TABLE:
                    if probe.ref not in missing_tables:
                        missing_tables.append(probe.ref)
                elif code == UNKNOWN_COLUMN:
                    missing_columns.append(probe)
                elif code is None or code >= _CLIENT_ERRORS:
                    raise  # connection trouble says nothing about grants
                # Any other server error: the privilege check passed, the statement
                # failed later (e.g. 1364 on the INSERT form) -- the right is there.
            finally:
                conn.rollback()
    return GrantReport(
        tuple(missing_rights), tuple(missing_tables), tuple(missing_columns), f"'{user}'@'{host}'"
    )
