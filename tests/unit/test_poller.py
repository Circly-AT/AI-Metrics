from dataclasses import dataclass, replace

import polars as pl
import pytest

from aim.kernel.jobs import JobState, MetricJob
from aim.kernel.ports import EVAL_SCHEMA
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


class FakeReader:
    def __init__(self, result: pl.DataFrame | BaseException) -> None:
        self.result = result
        self.calls: list[dict] = []

    def load(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeWriter:
    def __init__(self) -> None:
        self.written: dict[int, tuple] = {}

    def write(self, job, config, rows):
        self.written[job.id] = (config, list(rows))


def _poller(store, reader, writer=None):
    return Poller(
        jobs=store,
        reader=reader,
        writer=writer or FakeWriter(),
        config=PollerConfig(owner="me", poll_interval=0, backoff_initial=0, backoff_max=0),
        is_instance_fault=lambda exc: isinstance(exc, InstanceFault),
    )


JOB = MetricJob(id=1, cid="c1", configuration='{"aiJobId": 42}')


def test_a_triggered_job_is_claimed_computed_and_finished():
    store, reader, writer = FakeJobStore(JOB), FakeReader(_frame(3)), FakeWriter()
    assert _poller(store, reader, writer).tick() is True
    assert store.rows[1].state is JobState.finished
    assert reader.calls == [{"cid": "c1", "ai_job_id": 42}]
    config, rows = writer.written[1]
    assert config.ai_job_id == 42
    assert {r.metric for r in rows} == {"wape", "mae", "rmse", "bias"}


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
    [None, "", "not json", '{"aiJobId": "42"}', '{"aiJobId": 0}', '{"aiJobId": 42, "x": 1}'],
)
def test_an_unusable_config_ends_the_job_as_error_management_without_reading(configuration):
    store, reader = FakeJobStore(replace(JOB, configuration=configuration)), FakeReader(_frame(1))
    assert _poller(store, reader).tick() is True
    assert store.rows[1].state is JobState.error_management
    assert "jobConfiguration" in store.rows[1].error
    assert reader.calls == []


def test_a_job_fault_ends_the_job_as_error_with_the_message():
    store = FakeJobStore(JOB)
    assert _poller(store, FakeReader(_frame(0))).tick() is True
    assert store.rows[1].state is JobState.error
    assert "NoForecastError" in store.rows[1].error


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
