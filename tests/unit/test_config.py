from datetime import date

import pytest

from aim.boundaries.db.forecast_reader import SELECTIONS
from aim.kernel.jobs import (
    ConfigError,
    ForecastSelection,
    MetricJob,
    Selection,
    Window,
    parse_config,
)

TODAY = date(2026, 10, 15)


def _parse(configuration: str | None):
    return parse_config(MetricJob(id=1, cid="c1", configuration=configuration))


def _window(configuration: str | None) -> Window:
    return _parse(configuration).window(TODAY)


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
        '{"forecastSelection": "latest"}',
        '{"forecastSelection": "ai_job"}',
        '{"forecastSelection": "ai_job", "aiJobId": "42"}',
        '{"forecastSelection": "ai_job", "aiJobId": 0}',
        '{"aiJobId": 42}',
        '{"forecastSelection": "latest_before_day", "aiJobId": 42}',
    ],
)
def test_an_unusable_config_is_a_config_error(configuration):
    with pytest.raises(ConfigError, match="invalid jobConfiguration"):
        _window(configuration)


def test_the_default_forecast_selection_is_latest_before_day():
    assert _parse(None).forecast_selection is ForecastSelection.latest_before_day
    assert _parse('{"forecastSelection": "latest_before_day"}').forecast_selection is (
        ForecastSelection.latest_before_day
    )


def test_every_forecast_selection_has_exactly_one_query():
    """A strategy accepted in jobConfiguration but missing in the reader would pass the
    config check and then crash with KeyError mid-job."""
    assert set(SELECTIONS) == set(ForecastSelection)


def test_ai_job_names_the_job_it_evaluates():
    config = _parse('{"forecastSelection": "ai_job", "aiJobId": 42, "windowDays": 7}')
    assert config.selection == Selection(ForecastSelection.ai_job, ai_job_id=42)
    assert config.selection.params == {"ai_job_id": 42}
    assert config.selection.label == "ai_job:42"
    assert config.window(TODAY) == Window(date(2026, 10, 8), date(2026, 10, 14))


def test_latest_before_day_has_no_parameters_and_its_bare_name_as_label():
    selection = _parse(None).selection
    assert selection.params == {}
    assert selection.label == "latest_before_day"


@pytest.mark.parametrize(
    "selection",
    [Selection(ForecastSelection.latest_before_day), Selection(ForecastSelection.ai_job, 1)],
)
def test_every_query_binds_its_strategys_parameters(selection):
    """A parameter in `Selection.params` the query never uses (or the other way round)
    means the strategy silently ignores its configuration."""
    sql = SELECTIONS[selection.strategy]
    for name in selection.params:
        assert f":{name}" in sql
    assert (":ai_job_id" in sql) == ("ai_job_id" in selection.params)


def test_the_run_label_fits_the_forecast_selection_column():
    """`ai-metric-runs.forecastSelection` is varchar(32)."""
    assert len(Selection(ForecastSelection.ai_job, 2**31 - 1).label) <= 32
