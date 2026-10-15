from datetime import date

import pytest

from aim.kernel.jobs import ConfigError, MetricJob, Window, parse_config

TODAY = date(2026, 10, 15)


def _window(configuration: str | None) -> Window:
    return parse_config(MetricJob(id=1, cid="c1", configuration=configuration)).window(TODAY)


@pytest.mark.parametrize("configuration", [None, "", "  ", "{}"])
def test_an_empty_config_is_the_last_30_complete_days(configuration):
    window = _window(configuration)
    assert window == Window(date(2026, 9, 15), date(2026, 10, 14))
    assert window.days == 30


def test_window_days_moves_the_start_but_still_ends_yesterday():
    assert _window('{"windowDays": 7}') == Window(date(2026, 10, 8), date(2026, 10, 14))


def test_an_explicit_window_is_taken_as_given():
    window = _window('{"start": "2026-09-01", "end": "2026-09-30"}')
    assert window == Window(date(2026, 9, 1), date(2026, 9, 30))


def test_an_explicit_window_may_not_reach_into_today():
    with pytest.raises(ConfigError, match="not a complete day"):
        _window('{"start": "2026-10-01", "end": "2026-10-15"}')


@pytest.mark.parametrize(
    "configuration",
    [
        "not json",
        '{"windowDays": "30"}',
        '{"windowDays": 0}',
        '{"windowDays": 30, "x": 1}',
        '{"start": "2026-09-01"}',
        '{"start": "2026-09-30", "end": "2026-09-01"}',
        '{"windowDays": 30, "start": "2026-09-01", "end": "2026-09-30"}',
    ],
)
def test_an_unusable_config_is_a_config_error(configuration):
    with pytest.raises(ConfigError, match="invalid jobConfiguration"):
        _window(configuration)
