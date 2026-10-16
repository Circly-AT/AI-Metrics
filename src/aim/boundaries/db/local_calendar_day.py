"""Where the time zone ends: UTC instants of the product database <-> the tenant's local
calendar day (ADR 0008). Ported from AI-Pipeline-V3's module of the same name, so both
services put a sale and a forecast on the same day.

The product database stores sales (`orderDate`), forecast days (`prediction_values.date`)
and `ai-results.created_at` as naive UTC instants; a job counts in the tenant's local
calendar days. Only this module converts, and only with polars: one zone database in
both directions, as in the pipeline (whose write side uses `local_midnight_as_utc`).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import polars as pl

from aim.kernel.jobs import Window
from aim.kernel.ports import TenantTimezoneError

_ONE_MICROSECOND = timedelta(microseconds=1)


def to_local_day_expr(expr: pl.Expr, iana_tz: str) -> pl.Expr:
    """A naive UTC instant -> the tenant's local calendar day (`Date`). The pipeline's
    read-side expression: stamp as UTC, convert to the zone, take the date."""
    return expr.dt.replace_time_zone("UTC").dt.convert_time_zone(iana_tz).dt.date()


def local_midnight_as_utc_expr(expr: pl.Expr, iana_tz: str) -> pl.Expr:
    """A naive local midnight -> the naive UTC instant the tenant's day begins at. The
    pipeline's write-side expression (`encode_product_day`), verbatim:
    - midnight occurs TWICE when the clocks fall back across it: the first one;
    - it does not exist when the clocks jump over it: the day begins where the gap
      ends, i.e. one microsecond after the last moment of the previous day."""
    direct = expr.dt.replace_time_zone(iana_tz, ambiguous="earliest", non_existent="null")
    just_before = (expr - _ONE_MICROSECOND).dt.replace_time_zone(
        iana_tz, ambiguous="earliest", non_existent="null"
    )
    return pl.coalesce(
        direct.dt.convert_time_zone("UTC").dt.replace_time_zone(None),
        just_before.dt.convert_time_zone("UTC").dt.replace_time_zone(None) + _ONE_MICROSECOND,
    )


def local_day_of(now: datetime, iana_tz: str) -> date:
    """The tenant's calendar day at the aware instant `now`."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    naive_utc = now.astimezone(UTC).replace(tzinfo=None)
    frame = pl.DataFrame({"t": [naive_utc]}, schema={"t": pl.Datetime("us")})
    return frame.select(to_local_day_expr(pl.col("t"), iana_tz)).item()


@dataclass(frozen=True)
class LocalDay:
    """One local calendar day as the half-open UTC range `[start, end)` it covers."""

    day: date
    start: datetime
    end: datetime


def local_days(window: Window, iana_tz: str) -> tuple[LocalDay, ...]:
    """Every day of the window with its UTC range. Each day ends where the next begins,
    so every instant belongs to exactly one day -- 23 or 25 hours long on a DST change."""
    midnights = [
        datetime.combine(window.start + timedelta(days=i), time.min)
        for i in range(window.days + 1)
    ]
    frame = pl.DataFrame({"m": midnights}, schema={"m": pl.Datetime("us")})
    starts = frame.select(local_midnight_as_utc_expr(pl.col("m"), iana_tz))["m"].to_list()
    return tuple(
        LocalDay(m.date(), start, end)
        for m, start, end in zip(midnights, starts, starts[1:], strict=False)
    )


def require_usable_timezone(name: str | None, *, cid: str) -> str:
    """`name` if both conversions above can use it, else `TenantTimezoneError`. Exact on
    purpose (no trimming or case folding): a name that needs repair is a broken master
    record to report, not to guess at. The empty name needs its own check: polars would
    convert it as UTC without a word (measured by the pipeline, V3-175)."""
    if not name:
        raise TenantTimezoneError(f"cid {cid!r} has an empty timezone name ({name!r})")
    probe = pl.DataFrame({"t": [datetime(2000, 1, 1)]})
    try:
        probe.select(to_local_day_expr(pl.col("t"), name))
        probe.select(local_midnight_as_utc_expr(pl.col("t"), name))
    except pl.exceptions.PolarsError as exc:
        raise TenantTimezoneError(
            f"cid {cid!r} has timezone {name!r}, which is not an IANA zone as written"
        ) from exc
    return name
