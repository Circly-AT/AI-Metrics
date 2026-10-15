"""`data-jobs` reads and writes, restricted to `type = 'metrics'`.

Claim = a CONDITIONAL UPDATE, never `SELECT ... FOR UPDATE` (same as AI-Pipeline-V3's
`ai_job_claimer`): InnoDB evaluates the UPDATE's WHERE against the newest committed row,
so of two instances racing for one row the second matches 0 rows and moves on.
`containerID` is the claim owner. `version` is left alone, as the pipeline does on
`ai-jobs` (M6 7.3 variant A).
"""

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aim.kernel.jobs import JOB_TYPE, JobState, MetricJob
from aim.kernel.ports import FailState

LIST_CLAIMABLE = """SELECT id, cid, jobConfiguration FROM `data-jobs`
 WHERE type = :type AND state = :trigger AND deleted_at IS NULL
 ORDER BY priority ASC, created_at ASC, id ASC
 LIMIT :limit"""

CLAIM = """UPDATE `data-jobs`
   SET state = :detected, containerID = :owner, error = NULL
 WHERE id = :id AND type = :type AND state = :trigger AND deleted_at IS NULL"""

# Only the owner of a live claim may end it.
END = """UPDATE `data-jobs`
   SET state = :to_state, error = :message
 WHERE id = :id AND type = :type AND state = :detected AND containerID = :owner
   AND deleted_at IS NULL"""

# Owner-agnostic on purpose: a restarted instance finds its own orphans, a sister's fresh
# claim is protected by the grace. UTC_TIMESTAMP needs the session pinned to UTC (engine.py).
SWEEP_STALE = """UPDATE `data-jobs`
   SET state = :error_management,
       error = CONCAT('claim abandoned: owner ', COALESCE(containerID, '?'),
                      ' did not finish within ', :grace, 's')
 WHERE type = :type AND state = :detected AND deleted_at IS NULL
   AND updated_at < UTC_TIMESTAMP(6) - INTERVAL :grace SECOND"""


class DbJobStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_claimable(self, *, limit: int) -> Sequence[MetricJob]:
        params = {"type": JOB_TYPE, "trigger": JobState.trigger, "limit": limit}
        with self._engine.connect() as conn:
            rows = conn.execute(text(LIST_CLAIMABLE), params).all()
        return tuple(MetricJob(id=int(r[0]), cid=r[1], configuration=r[2]) for r in rows)

    def claim(self, job_id: int, *, owner: str) -> bool:
        params = {
            "id": job_id, "owner": owner, "type": JOB_TYPE,
            "trigger": JobState.trigger, "detected": JobState.job_detected,
        }
        return self._update(CLAIM, params) == 1

    def finish(self, job_id: int, *, owner: str) -> bool:
        return self._end(job_id, owner, JobState.finished, None)

    def fail(self, job_id: int, *, owner: str, state: FailState, message: str) -> bool:
        return self._end(job_id, owner, JobState(state), message)

    def sweep_stale(self, *, grace_seconds: int) -> int:
        params = {
            "type": JOB_TYPE, "detected": JobState.job_detected,
            "error_management": JobState.error_management, "grace": grace_seconds,
        }
        return self._update(SWEEP_STALE, params)

    def _end(self, job_id: int, owner: str, to_state: JobState, message: str | None) -> bool:
        params = {
            "id": job_id, "owner": owner, "type": JOB_TYPE, "to_state": to_state,
            "message": message, "detected": JobState.job_detected,
        }
        return self._update(END, params) == 1

    def _update(self, statement: str, params: dict[str, object]) -> int:
        with self._engine.begin() as conn:
            return conn.execute(text(statement), params).rowcount
