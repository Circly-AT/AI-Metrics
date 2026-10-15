# AI-Metrics (`aim`)

Computes accuracy metrics for AI-Pipeline-V3 forecast runs. A management service polls
`circly_data_handling.data-jobs` for `type = 'metrics'` rows, claims one, compares the
referenced run's point forecasts (`prediction_values`) with actual sales and writes the
metrics to `ai-metric-results`. `abc_xyz` rows are not touched.

`jobConfiguration` of a metrics job (assumed, not yet agreed with the backend):

```json
{"aiJobId": 123}
```

Parsed strictly: a missing/empty value, a string instead of a number, or an unknown key
ends the job as `error_management` before any data is read.

## Run

```bash
uv sync
cp .env.example .env   # fill in DB_*
uv run aim serve --env-file .env          # poll until SIGTERM/SIGINT
uv run aim serve --env-file .env --once   # one poll, then exit
```

## Checks

```bash
uv run pytest          # unit tests (no DB needed)
uv run lint-imports    # layer contract
uv run ruff check
```

## Layout

Layers, enforced by import-linter (`pyproject.toml`). Simplified from AI-Pipeline-V3's
`apps → coordination → execution → boundaries | engine → kernel`:

```
src/aim/
  apps/        cli.py            entry point `aim serve`, wires everything together
  service/     poller.py         the loop: sweep stale → list trigger → claim → run → finish/fail
               runner.py         one job: parse config → load → evaluate → write
  boundaries/  settings.py       env reader (same DB_* names as the pipeline)
               db/job_store.py   data-jobs (type=metrics): conditional-UPDATE claim, sweep
               db/forecast_reader.py   prediction_values + sales → forecast vs. actual
               db/metric_writer.py     ai-metric-results
  metrics/     point.py          wape, mae, rmse, bias (same formulas as the pipeline)
               evaluate.py       per (article, account) + pooled total
  kernel/      jobs.py (states, jobConfiguration model), results.py, ports.py (Protocols)
ddl/           proposed ai-metric-results table (not yet agreed with the backend)
```

`service`, `metrics` and `kernel` never import a DB driver, so the poller is tested
against in-memory fakes (`tests/unit/test_poller.py`).

## Job lifecycle

```
trigger ──claim──▶ job_detected ──▶ finished
                        ├─────────▶ error             (metric computation failed)
                        ├─────────▶ error_management  (bad jobConfiguration)
                        └──stale──▶ error_management  (owner stopped responding)
```

The message goes to `data-jobs.error`, the owner (`host:pid`) to `containerID`.

- **Claim** is `UPDATE … WHERE id = :id AND type = 'metrics' AND state = 'trigger'`;
  `rowcount == 1` means this instance owns it. Several instances can run side by side.
- **Job fault** (no forecasts, bad data, a bug in a metric): `error`, the loop goes on.
- **Instance fault** (DB unreachable): the claim stays, the loop backs off exponentially
  (5 s → 300 s). After `STALE_GRACE_SECONDS` without a row change the sweep ends the claim
  as `error_management`. `data-jobs` has no attempts counter, so there is no automatic
  retry; re-triggering = set `state` back to `trigger`.

## Open questions

1. **`jobConfiguration` shape**: is `{"aiJobId": …}` right? Alternatives: several runs per
   job, or the link via `ai-jobs.dataJobId` (that FK exists, but points the other way and
   may mean "this forecast consumed this data-job").
2. **`error` vs. `error_management`**: the split above is my reading; confirm it matches
   what the backend/frontend expects.
3. **Results table**: `ddl/ai-metric-results.sql` is a proposal, the backend has to agree to
   it (and to grants: SELECT/UPDATE on `data-jobs`, SELECT on `prediction_values`,
   `ai-results`, `sales`, DELETE/INSERT on the results table).
4. **Day boundaries and filters**: the reader aggregates sales per UTC day with
   `type = 'sales'`. The pipeline converts to the tenant's time zone and applies
   return/special-sale filters from the job config. To match its numbers, read those from
   `ai-jobs.jobConfiguration`.
5. **Granularity**: forecasts are assumed to be daily. Weekly/monthly forecasts need the
   actuals bucketed the same way.
6. **Partial horizons**: a job run before the horizon ends counts days without sales yet as
   actual 0. Either only evaluate up to "yesterday" or only create jobs after the horizon.
7. **More metrics**: MASE needs the training series; quantile forecasts (pinball loss,
   coverage) need `quantile IS NOT NULL` rows.
8. **Long jobs**: there is no heartbeat, so `STALE_GRACE_SECONDS` must exceed the slowest job.
