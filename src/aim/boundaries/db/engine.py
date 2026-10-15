from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError

# Pin the session to UTC: the stale sweep compares `updated_at` (session time) with
# UTC_TIMESTAMP(6). Same reasoning as AI-Pipeline-V3's `_DB_SESSION_INIT`.
_SESSION_INIT = "SET time_zone = '+00:00'"


def create_db_engine(url: str) -> Engine:
    """`pool_pre_ping`: MariaDB's wait_timeout cuts idle connections between polls.
    SQLAlchemy's pymysql dialect reports MATCHED rows (CLIENT.FOUND_ROWS), which is what
    the conditional UPDATEs in `job_store` rely on -- so no `client_flag` here."""
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"init_command": _SESSION_INIT},
    )


def is_instance_fault(exc: BaseException) -> bool:
    """Lost/unreachable DB = the instance is sick, not the job. Retrying later helps."""
    return isinstance(exc, OperationalError) or (
        isinstance(exc, DBAPIError) and exc.connection_invalidated
    )
