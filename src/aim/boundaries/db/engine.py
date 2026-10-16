from collections.abc import Mapping

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError

from aim.boundaries.db.driver_errors import PERMANENT, errno_of

# Pin the session to UTC: the stale sweep compares `updated_at` (session time) with
# UTC_TIMESTAMP(6). Same reasoning as AI-Pipeline-V3's `_DB_SESSION_INIT`.
_SESSION_INIT = "SET time_zone = '+00:00'"

# Without it pymysql waits forever on a half-open connection or a metadata lock. Far above
# InnoDB's lock wait (50 s), so a real lock wait still ends as the lock error it is.
_READ_TIMEOUT_SECONDS = 600


def create_db_engine(url: str, *, tls_args: Mapping[str, object]) -> Engine:
    """`tls_args` is REQUIRED and comes from `Settings.db_tls_args`: leaving TLS out is not
    the safe default but pymysql's unverified mode (see `Settings.db_tls`).

    `pool_pre_ping`: MariaDB's wait_timeout cuts idle connections between polls.
    SQLAlchemy's pymysql dialect reports MATCHED rows (CLIENT.FOUND_ROWS), which is what
    the conditional UPDATEs in `job_store` rely on -- so no `client_flag` here."""
    if not tls_args:
        raise ValueError("tls_args is empty: that is pymysql's unverified TLS mode")
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={
            **tls_args,
            "init_command": _SESSION_INIT,
            "read_timeout": _READ_TIMEOUT_SECONDS,
        },
    )


def is_permanent_fault(exc: BaseException) -> bool:
    """A DB error that only a grant, DDL or settings change fixes."""
    return errno_of(exc) in PERMANENT


def is_instance_fault(exc: BaseException) -> bool:
    """Lost/unreachable DB = the instance is sick, not the job: back off, retry later.

    NOT a missing grant, table or schema, or wrong credentials (`PERMANENT`): pymysql
    raises those as `OperationalError` too, but waiting never fixes them. They escape the
    loop, so `aim serve` stops with exit 1 instead of backing off forever."""
    if is_permanent_fault(exc):
        return False
    return isinstance(exc, OperationalError) or (
        isinstance(exc, DBAPIError) and exc.connection_invalidated
    )
