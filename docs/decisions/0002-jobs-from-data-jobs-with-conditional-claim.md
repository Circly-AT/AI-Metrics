# 0002 — Jobs come from `data-jobs`, claimed by a conditional UPDATE

**Status:** accepted · 2026-10-15

## Context

The backend already has `circly_data_handling.data-jobs` with `type = 'metrics'` (contract:
[data-jobs](../contracts/data-jobs.md)). A first draft proposed an own `ai-metric-jobs`
table with an attempts counter; dropped once the existing table was known. Several AIM
instances may run at once.

## Decision

1. **Source:** `data-jobs` rows with `type = 'metrics'`; AIM writes `state`, `containerID`
   and `error` only.
2. **Claim:** `UPDATE … SET state = 'job_detected', containerID = :owner WHERE id = :id AND
   type = 'metrics' AND state = 'trigger'`. `rowcount == 1` = this instance owns it. No
   `SELECT … FOR UPDATE` — same pattern and reasoning as the pipeline's `ai_job_claimer`
   (InnoDB evaluates the UPDATE's WHERE against the newest committed row).
3. **Ending** a job also checks `state = 'job_detected' AND containerID = :owner`, so an
   instance that lost its claim cannot overwrite the row.
4. **Three fault classes:**
   - `ConfigError` (unusable `jobConfiguration`) → `error_management`, loop goes on.
   - Any other exception from the job → `error`, loop goes on.
   - Instance fault (`OperationalError`, invalidated connection) → claim stays, loop backs
     off 5 s → 300 s, doubling.
5. **Stale claims:** a `job_detected` row unchanged for `STALE_GRACE_SECONDS` is ended as
   `error_management` by any instance. No automatic retry — the table has no attempts
   counter, and re-triggering is the backend's call.
6. **Session in UTC** (`SET time_zone = '+00:00'` on connect), because the sweep compares
   `updated_at` with `UTC_TIMESTAMP(6)`.

## Consequences

- No heartbeat: a job longer than the grace is ended under a live owner. Its own end then
  is refused (owner check) and its results may still have been written. Keep the grace
  well above the slowest job, or add a heartbeat when jobs get long.
- `error` vs. `error_management` is our reading of the enum; to be confirmed with the
  backend.
