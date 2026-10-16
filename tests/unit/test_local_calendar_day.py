from datetime import UTC, date, datetime, timedelta

import pytest

from aim.boundaries.db.forecast_reader import (
    ACTUALS,
    SELECTIONS,
    days_table,
    tenant_timezone,
)
from aim.boundaries.db.local_calendar_day import (
    LocalDay,
    local_day_of,
    local_days,
    require_usable_timezone,
)
from aim.kernel.jobs import Window
from aim.kernel.ports import TenantTimezoneError

VIENNA = "Europe/Vienna"


def test_a_vienna_day_begins_at_local_midnight_in_utc():
    """What the pipeline writes into prediction_values.date: 1 June = 31 May 22:00 UTC.
    Mutation: `DATE(pv.date)` as before => the forecast lands on 31 May."""
    (day,) = local_days(Window(date(2026, 6, 1), date(2026, 6, 1)), VIENNA)
    assert day == LocalDay(date(2026, 6, 1), datetime(2026, 5, 31, 22), datetime(2026, 6, 1, 22))
    (winter,) = local_days(Window(date(2026, 1, 15), date(2026, 1, 15)), VIENNA)
    assert winter.start == datetime(2026, 1, 14, 23)


def test_days_are_contiguous_and_follow_the_clock_changes():
    spring = local_days(Window(date(2026, 3, 28), date(2026, 3, 30)), VIENNA)
    autumn = local_days(Window(date(2026, 10, 24), date(2026, 10, 26)), VIENNA)
    for days in (spring, autumn):
        assert all(a.end == b.start for a, b in zip(days, days[1:], strict=False))
    assert [d.end - d.start for d in spring] == [timedelta(hours=h) for h in (24, 23, 24)]
    assert [d.end - d.start for d in autumn] == [timedelta(hours=h) for h in (24, 25, 24)]


def test_a_day_without_a_local_midnight_begins_where_the_gap_ends():
    """Havana 2016-03-13: the clocks jump from 00:00 to 01:00 (= 05:00 UTC)."""
    _, gap_day = local_days(Window(date(2016, 3, 12), date(2016, 3, 13)), "America/Havana")
    assert gap_day.start == datetime(2016, 3, 13, 5)


def test_a_double_midnight_takes_the_first():
    """Havana 2025-11-02: 01:00 falls back to 00:00, so midnight occurs at 04:00 and
    05:00 UTC; the day begins at the first."""
    _, double = local_days(Window(date(2025, 11, 1), date(2025, 11, 2)), "America/Havana")
    assert double.start == datetime(2025, 11, 2, 4)


@pytest.mark.parametrize(
    ("now", "zone", "today"),
    [
        (datetime(2026, 10, 15, 22, 30, tzinfo=UTC), VIENNA, date(2026, 10, 16)),
        (datetime(2026, 10, 15, 21, 59, tzinfo=UTC), VIENNA, date(2026, 10, 15)),
        (datetime(2026, 10, 15, 3, 30, tzinfo=UTC), "America/Phoenix", date(2026, 10, 14)),
    ],
)
def test_today_is_the_tenants_local_day(now, zone, today):
    assert local_day_of(now, zone) == today


def test_a_naive_now_is_refused():
    with pytest.raises(ValueError, match="aware"):
        local_day_of(datetime(2026, 10, 15, 22, 30), VIENNA)


@pytest.mark.parametrize("name", ["", None, "Europe/Wien", "europe/vienna "])
def test_an_unusable_zone_name_is_refused_not_read_as_utc(name):
    with pytest.raises(TenantTimezoneError, match="c1"):
        require_usable_timezone(name, cid="c1")


@pytest.mark.parametrize("name", [VIENNA, "America/New_York", "+01:00"])
def test_a_usable_zone_name_passes(name):
    assert require_usable_timezone(name, cid="c1") == name


class FakeConn:
    def __init__(self, row):
        self.row = row

    def execute(self, clause, params):
        return self

    def first(self):
        return self.row


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (None, "no live circly_customers.companies row"),
        ((None, VIENNA), "soft-deleted zone row 'Europe/Vienna'"),
        ((None, None), "no timezones row at all"),
        (("", None), "empty timezone name"),
    ],
)
def test_a_broken_tenant_record_is_a_named_error(row, message):
    with pytest.raises(TenantTimezoneError, match=message):
        tenant_timezone(FakeConn(row), "c1")


def test_the_days_table_binds_every_value_and_formats_none():
    days = local_days(Window(date(2026, 6, 1), date(2026, 6, 2)), VIENNA)
    table, params = days_table(days)
    assert table.count("SELECT") == 2 and "UNION ALL" in table
    assert "2026" not in table  # values are parameters, never SQL text
    assert params["day_1"] == date(2026, 6, 2)
    assert params["day_start_0"] == datetime(2026, 5, 31, 22)
    assert params["day_end_1"] == datetime(2026, 6, 2, 22)


@pytest.mark.parametrize("sql", [*SELECTIONS.values(), ACTUALS])
def test_no_query_takes_the_utc_date_of_an_instant(sql):
    """A day comes from the days table only. Mutation: put `DATE(pv.date)` back => red."""
    assert "{days}" in sql
    assert "DATE(" not in sql.replace("AS DATE)", "")
