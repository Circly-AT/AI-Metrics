"""MariaDB error numbers AIM tells apart. The errno decides, never the exception class:
pymysql maps a missing grant (1142) onto `OperationalError` just like a lost connection
(2013), so the class alone cannot say whether waiting helps."""

ACCESS_DENIED_TO_SCHEMA = 1044
ACCESS_DENIED_FOR_USER = 1045  # wrong user/password
UNKNOWN_DATABASE = 1049
TABLE_ACCESS_DENIED = 1142  # e.g. "UPDATE command denied ... for table"
COLUMN_ACCESS_DENIED = 1143
NO_SUCH_TABLE = 1146
UNKNOWN_COLUMN = 1054  # the table exists, but not as AIM's statements expect it
DUPLICATE_KEY = 1062
DEADLOCK = 1213  # InnoDB rolled the transaction back; running it again is the remedy

PERMANENT = frozenset(
    {
        ACCESS_DENIED_TO_SCHEMA,
        ACCESS_DENIED_FOR_USER,
        UNKNOWN_DATABASE,
        TABLE_ACCESS_DENIED,
        COLUMN_ACCESS_DENIED,
        NO_SUCH_TABLE,
        UNKNOWN_COLUMN,
    }
)
"""Configuration errors: retrying does not fix them, a grant/DDL/settings change does."""


def errno_of(exc: BaseException) -> int | None:
    """The driver's error number behind a SQLAlchemy exception, or None."""
    args = getattr(getattr(exc, "orig", None), "args", ()) or ()
    code = args[0] if args else None
    return code if isinstance(code, int) else None
