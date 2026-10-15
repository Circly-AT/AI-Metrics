"""The serve loop: sweep stale claims, claim ONE `trigger` metrics job, run it, mark it.

Fault classes, as in AI-Pipeline-V3's `job_poller`:
- CONFIG fault (`ConfigError`): the job ends as `error_management`; the loop goes on.
- JOB fault (anything else `run_job` raises that is not an instance fault): the job ends
  as `error` with the message; the loop goes on.
- INSTANCE fault (DB down, connection lost -- decided by the injected `is_instance_fault`):
  the claim is left as it is (the stale sweep ends it after the grace period) and the
  loop backs off exponentially.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from aim.kernel.jobs import ConfigError
from aim.kernel.ports import ForecastReader, JobStore, MetricWriter
from aim.service.runner import run_job

_LOGGER = logging.getLogger(__name__)


@dataclass
class Backoff:
    initial: float
    maximum: float
    current: float = field(init=False)

    def __post_init__(self) -> None:
        self.current = self.initial

    def escalate(self) -> float:
        wait = self.current
        self.current = min(self.current * 2, self.maximum)
        return wait

    def reset(self) -> None:
        self.current = self.initial


@dataclass(frozen=True)
class PollerConfig:
    owner: str
    poll_interval: float = 30.0
    stale_grace_seconds: int = 3600
    backoff_initial: float = 5.0
    backoff_max: float = 300.0


class Poller:
    def __init__(
        self,
        *,
        jobs: JobStore,
        reader: ForecastReader,
        writer: MetricWriter,
        config: PollerConfig,
        is_instance_fault: Callable[[BaseException], bool],
        stop: threading.Event | None = None,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        self._jobs = jobs
        self._reader = reader
        self._writer = writer
        self._config = config
        self._is_instance_fault = is_instance_fault
        self.stop = stop or threading.Event()
        self._today = today
        self._backoff = Backoff(config.backoff_initial, config.backoff_max)

    def tick(self) -> bool:
        """One poll. True when a job was claimed (finished or failed), False when there was
        nothing to claim. Instance faults propagate to `serve`."""
        ended = self._jobs.sweep_stale(grace_seconds=self._config.stale_grace_seconds)
        if ended:
            _LOGGER.warning("ended %d abandoned claim(s) as error_management", ended)

        owner = self._config.owner
        # A few candidates, so a sister instance winning the first one is not a wasted poll.
        for job in self._jobs.list_claimable(limit=5):
            if not self._jobs.claim(job.id, owner=owner):
                continue
            _LOGGER.info("claimed data-job %d (cid %s)", job.id, job.cid)
            try:
                run_job(job, reader=self._reader, writer=self._writer, today=self._today())
            except ConfigError as exc:
                _LOGGER.error("data-job %d: %s", job.id, exc)
                self._jobs.fail(job.id, owner=owner, state="error_management", message=str(exc))
            except Exception as exc:
                if self._is_instance_fault(exc):
                    raise
                _LOGGER.exception("data-job %d failed", job.id)
                self._jobs.fail(
                    job.id, owner=owner, state="error", message=f"{type(exc).__name__}: {exc}"
                )
            else:
                self._jobs.finish(job.id, owner=owner)
            return True
        return False

    def serve(self, *, max_ticks: int | None = None) -> None:
        """Polls until `stop` is set. After a job, poll again at once (there may be more);
        after an empty poll, wait `poll_interval`; after an instance fault, back off."""
        ticks = 0
        while not self.stop.is_set() and (max_ticks is None or ticks < max_ticks):
            ticks += 1
            try:
                worked = self.tick()
            except Exception as exc:
                if not self._is_instance_fault(exc):
                    raise
                wait = self._backoff.escalate()
                _LOGGER.error("instance fault (%s); backing off %.0fs", exc, wait)
                self.stop.wait(wait)
                continue
            self._backoff.reset()
            if not worked:
                self.stop.wait(self._config.poll_interval)
