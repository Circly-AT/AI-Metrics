from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

import polars as pl
import pytest

from aim.kernel.jobs import ForecastSelection, JobState, MetricJob, Selection, Window
from aim.kernel.ports import EVAL_SCHEMA, TenantTimezoneError
from aim.service.poller import Backoff, Poller, PollerConfig


class InstanceFault(Exception):
    pass


@dataclass
class Row:
    job: MetricJob
    state: JobState = JobState.trigger
    owner: str | None = None
    error: str | None = None
    stale: bool = False


class FakeJobStore:
    """In-memory twin of DbJobStore with the same conditional-update semantics."""

    def __init__(self, *jobs: MetricJob) -> None:
        self.rows = {j.id: Row(j) for j in jobs}

    def list_claimable(self, *, limit):
        return [r.job for r in self.rows.values() if r.state is JobState.trigger][:limit]

    def claim(self, job_id, *, owner):
        row = self.rows[job_id]
        if row.state is not JobState.trigger:
            return False
        row.state, row.owner = JobState.job_detected, owner
        return True

    def finish(self, job_id, *, owner):
        return self._end(job_id, owner, JobState.finished, None)

    def fail(self, job_id, *, owner, state, message):
        return self._end(job_id, owner, JobState(state), message)

    def sweep_stale(self, *, grace_seconds):
        ended = 0
        for row in self.rows.values():
            if row.state is JobState.job_detected and row.stale:
                row.state, row.error = JobState.error_management, "claim abandoned"
                ended += 1
        return ended

    def _end(self, job_id, owner, to_state, message):
        row = self.rows[job_id]
        if row.state is not JobState.job_detected or row.owner != owner:
            return False
        row.state, row.error = to_state, message
        return True


def _frame(n: int) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "article_id": [1] * n,
            "account_id": [7] * n,
            "date": pl.date_range(pl.date(2026, 10, 1), pl.date(2026, 10, n), eager=True)
            if n
            else [],
            "forecast": [1.0] * n,
            "actual": [2.0] * n,
        },
        schema=EVAL_SCHEMA,
    )


NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


class FakeReader:
    def __init__(
        self,
        result: pl.DataFrame | BaseException,
        today: date | BaseException = date(2026, 10, 15),
        ai_job_days: Window | None = None,
    ) -> None:
        self.result = result
        self.ai_job_days_result = ai_job_days
        self.ai_job_days_calls: list[dict] = []
        self.today = today
        self.calls: list[dict] = []
        self.today_calls: list[dict] = []

    def local_today(self, **kwargs):
        self.today_calls.append(kwargs)
        if isinstance(self.today, BaseException):
            raise self.today
        return self.today

    def ai_job_days(self, **kwargs):
        self.ai_job_days_calls.append(kwargs)
        return self.ai_job_days_result

    def load(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeWriter:
    def __init__(self) -> None:
        self.written: dict[int, tuple] = {}

    def write(self, job, window, selection, rows):
        self.written[job.id] = (window, selection, list(rows))


def _poller(store, reader, writer=None):
    return Poller(
        jobs=store,
        reader=reader,
        writer=writer or FakeWriter(),
        config=PollerConfig(owner="me", poll_interval=0, backoff_initial=0, backoff_max=0),
        is_instance_fault=lambda exc: isinstance(exc, InstanceFault),
        now=lambda: NOW,
    )


JOB = MetricJob(id=1, cid="c1", configuration=None)  # what the backend writes today
LAST_30 = Window(date(2026, 9, 15), date(2026, 10, 14))


def test_a_triggered_job_is_claimed_computed_and_finished():
    store, reader, writer = FakeJobStore(JOB), FakeReader(_frame(3)), FakeWriter()
    assert _poller(store, reader, writer).tick() is True
    assert store.rows[1].state is JobState.finished
    latest = Selection(ForecastSelection.latest_before_day)
    assert reader.calls == [{"cid": "c1", "window": LAST_30, "selection": latest}]
    window, selection, rows = writer.written[1]
    assert (window, selection) == (LAST_30, latest)
    assert {r.metric for r in rows} == {"wape", "mae", "rmse", "bias"}


def _ai_job(extra: str = "") -> MetricJob:
    config = '{"forecastSelection": "ai_job", "aiJobId": 42' + extra + "}"
    return MetricJob(id=1, cid="c1", configuration=config)


def test_an_ai_job_without_a_window_takes_the_days_the_job_forecast():
    """Today is 15 Oct: the job forecast 1..28 Oct, so 1..14 Oct are evaluated."""
    reader = FakeReader(_frame(3), ai_job_days=Window(date(2026, 10, 1), date(2026, 10, 28)))
    store, writer = FakeJobStore(_ai_job()), FakeWriter()
    assert _poller(store, reader, writer).tick() is True
    assert store.rows[1].state is JobState.finished
    assert reader.ai_job_days_calls == [{"cid": "c1", "ai_job_id": 42}]
    selection = Selection(ForecastSelection.ai_job, ai_job_id=42)
    assert reader.calls == [
        {"cid": "c1", "window": Window(date(2026, 10, 1), date(2026, 10, 14)),
         "selection": selection}
    ]
    assert writer.written[1][1].label == "ai_job:42"


def test_an_ai_job_that_ended_in_the_past_is_evaluated_whole():
    days = Window(date(2026, 9, 1), date(2026, 9, 14))
    store, reader = FakeJobStore(_ai_job()), FakeReader(_frame(3), ai_job_days=days)
    assert _poller(store, reader).tick() is True
    assert reader.calls[0]["window"] == days


@pytest.mark.parametrize(
    ("days", "message"),
    [
        (None, "ai-job 42 has no point forecast"),
        (Window(date(2026, 10, 15), date(2026, 10, 28)), "no day of it is complete"),
    ],
)
def test_an_ai_job_without_a_complete_forecast_day_is_a_job_error(days, message):
    store, reader = FakeJobStore(_ai_job()), FakeReader(_frame(3), ai_job_days=days)
    assert _poller(store, reader).tick() is True
    assert store.rows[1].state is JobState.error
    assert message in store.rows[1].error
    assert reader.calls == []


@pytest.mark.parametrize(
    ("extra", "window"),
    [
        (', "windowDays": 7', Window(date(2026, 10, 8), date(2026, 10, 14))),
        (
            ', "start": "2026-09-01", "end": "2026-09-30"',
            Window(date(2026, 9, 1), date(2026, 9, 30)),
        ),
    ],
)
def test_an_explicit_window_overrides_the_ai_jobs_own(extra, window):
    store, reader = FakeJobStore(_ai_job(extra)), FakeReader(_frame(3))
    assert _poller(store, reader).tick() is True
    assert reader.ai_job_days_calls == []
    assert reader.calls[0]["window"] == window


def test_the_window_ends_the_day_before_the_tenants_today():
    """The tenant's local day, not the UTC one: late on 15 Oct UTC it is already 16 Oct in
    Vienna, so the window ends on the 15th. Mutation: compute today from `now` in UTC
    in the runner => red."""
    store, reader = FakeJobStore(JOB), FakeReader(_frame(3), today=date(2026, 10, 16))
    assert _poller(store, reader).tick() is True
    assert reader.today_calls == [{"cid": "c1", "now": NOW}]
    assert reader.calls[0]["window"] == Window(date(2026, 9, 16), date(2026, 10, 15))


def test_a_tenant_without_a_usable_timezone_ends_the_job_as_error():
    error = TenantTimezoneError("cid 'c1' has an empty timezone name ('')")
    store, reader = FakeJobStore(JOB), FakeReader(_frame(3), today=error)
    assert _poller(store, reader).tick() is True
    assert store.rows[1].state is JobState.error
    assert "empty timezone name" in store.rows[1].error
    assert reader.calls == []


def test_an_empty_queue_is_not_work():
    assert _poller(FakeJobStore(), FakeReader(_frame(3))).tick() is False


def test_a_job_lost_to_a_sister_instance_moves_on_to_the_next():
    second = replace(JOB, id=2)
    store = FakeJobStore(JOB, second)
    store.rows[1].state = JobState.job_detected  # a sister won it between list and claim
    store.list_claimable = lambda limit: [JOB, second]
    assert _poller(store, FakeReader(_frame(1))).tick() is True
    assert store.rows[2].state is JobState.finished
    assert store.rows[1].owner is None


@pytest.mark.parametrize(
    "configuration",
    ["not json", '{"windowDays": "30"}', '{"start": "2026-10-01", "end": "2026-10-15"}'],
)
def test_an_unusable_config_ends_the_job_as_error_management_without_reading(configuration):
    store, reader = FakeJobStore(replace(JOB, configuration=configuration)), FakeReader(_frame(1))
    assert _poller(store, reader).tick() is True
    assert store.rows[1].state is JobState.error_management
    assert store.rows[1].error  # the reason reaches data-jobs.error
    assert reader.calls == []


def test_a_job_fault_ends_the_job_as_error_with_the_message():
    store = FakeJobStore(JOB)
    assert _poller(store, FakeReader(_frame(0))).tick() is True
    assert store.rows[1].state is JobState.error
    assert "NoForecastError" in store.rows[1].error


def test_a_window_without_any_sale_ends_the_job_as_error_and_writes_nothing():
    store, writer = FakeJobStore(JOB), FakeWriter()
    no_sales = _frame(3).with_columns(pl.lit(0.0).alias("actual"))
    assert _poller(store, FakeReader(no_sales), writer).tick() is True
    assert store.rows[1].state is JobState.error
    assert "NoSalesError" in store.rows[1].error
    assert writer.written == {}


def test_a_single_sale_in_the_window_is_enough_to_compute():
    store = FakeJobStore(JOB)
    one_sale = _frame(3).with_columns(pl.Series("actual", [0.0, 0.0, 5.0]))
    assert _poller(store, FakeReader(one_sale)).tick() is True
    assert store.rows[1].state is JobState.finished


def test_an_instance_fault_leaves_the_claim_for_the_sweep():
    store = FakeJobStore(JOB)
    with pytest.raises(InstanceFault):
        _poller(store, FakeReader(InstanceFault("db gone"))).tick()
    assert store.rows[1].state is JobState.job_detected


def test_serve_survives_an_instance_fault_and_the_sweep_ends_the_abandoned_claim():
    store = FakeJobStore(JOB)
    poller = _poller(store, FakeReader(InstanceFault("db gone")))
    poller.serve(max_ticks=1)
    assert store.rows[1].state is JobState.job_detected

    store.rows[1].stale = True
    poller.serve(max_ticks=1)
    assert store.rows[1].state is JobState.error_management


def test_serve_lets_a_non_instance_fault_escape():
    store = FakeJobStore(JOB)
    store.sweep_stale = lambda **_: (_ for _ in ()).throw(RuntimeError("bug"))
    with pytest.raises(RuntimeError):
        _poller(store, FakeReader(_frame(1))).serve(max_ticks=1)


def test_backoff_doubles_up_to_the_maximum_and_resets():
    backoff = Backoff(initial=5, maximum=12)
    assert [backoff.escalate() for _ in range(4)] == [5, 10, 12, 12]
    backoff.reset()
    assert backoff.escalate() == 5


class PermanentFault(Exception):
    pass


def test_a_permanent_fault_ends_the_job_as_error_management_and_stops_the_loop():
    store = FakeJobStore(JOB)
    poller = Poller(
        jobs=store,
        reader=FakeReader(PermanentFault("UPDATE command denied")),
        writer=FakeWriter(),
        config=PollerConfig(owner="me", poll_interval=0, backoff_initial=0, backoff_max=0),
        is_instance_fault=lambda exc: False,
        is_permanent_fault=lambda exc: isinstance(exc, PermanentFault),
        now=lambda: NOW,
    )
    with pytest.raises(PermanentFault):
        poller.serve(max_ticks=3)
    assert store.rows[1].state is JobState.error_management
    assert "DB configuration error" in store.rows[1].error
