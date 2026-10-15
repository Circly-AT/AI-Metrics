# AI-Metrics (`aim`)

Computes forecast accuracy metrics per tenant. A management service polls
`circly_data_handling.data-jobs` for `type = 'metrics'` rows, claims one, compares the
tenant's point forecasts (`prediction_values`) over a window of past days with actual
sales and writes the metrics to `ai-metric-results`. `abc_xyz` rows are not touched.

### Which forecast counts for a day

Daily runs forecast the same day several times. For each (article, account, day) the
forecast that counts is the one from the **latest `ai-results` row written before that day
began** (`created_at < day 00:00`, ties to the higher id). A forecast written on the day
itself is ignored. The selection runs in SQL (`boundaries/db/forecast_reader.py`).

### `jobConfiguration`

Optional. Empty (what the backend writes today) means the defaults:

```json
{}                                            // last 30 complete days, ending yesterday
{"windowDays": 90}                            // last 90 days, ending yesterday
{"start": "2026-09-01", "end": "2026-09-30"}  // fixed window, both inclusive
```

Today is never evaluated (its sales are still arriving). Parsed strictly: an unknown key,
`"30"` instead of `30`, `start` without `end`, `windowDays` together with `start`/`end`, or
an `end` of today or later ends the job as `error_management` before any data is read.

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
               runner.py         one job: parse config → window → load → evaluate → write
  boundaries/  settings.py       env reader (same DB_* names as the pipeline)
               db/job_store.py   data-jobs (type=metrics): conditional-UPDATE claim, sweep
               db/forecast_reader.py   latest forecast before each day + sales → forecast vs. actual
               db/metric_writer.py     ai-metric-results
  metrics/     point.py          wape, mae, rmse, bias (same formulas as the pipeline)
               evaluate.py       per (article, account) + pooled total
  kernel/      jobs.py (states, jobConfiguration + window), results.py, ports.py (Protocols)
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
- **Job fault** (no forecast in the window, bad data, a bug in a metric): `error`, the loop
  goes on.
- **Instance fault** (DB unreachable): the claim stays, the loop backs off exponentially
  (5 s → 300 s). After `STALE_GRACE_SECONDS` without a row change the sweep ends the claim
  as `error_management`. `data-jobs` has no attempts counter, so there is no automatic
  retry; re-triggering = set `state` back to `trigger`.

## Open questions

1. **Forecast selection rule is untested against a DB**: it lives in SQL, and the unit tests
   use fakes. Needs an integration test on a seeded MariaDB (several overlapping runs, a
   forecast written on the target day itself, a tie on `created_at`).
2. **`error` vs. `error_management`**: the split above is my reading; confirm it matches
   what the backend/frontend expects.
3. **Results table**: `ddl/ai-metric-results.sql` is a proposal, the backend has to agree to
   it (and to grants: SELECT/UPDATE on `data-jobs`, SELECT on `prediction_values`,
   `ai-results`, `ai-jobs`, `sales`, DELETE/INSERT on the results table).
4. **Day boundaries and filters**: days are UTC days, both for `prediction_values.date`,
   `sales.orderDate` and the "written before the day" cut-off. The pipeline converts to the
   tenant's time zone and filters returns/special sales per config, so numbers can drift
   from its backtests until that is aligned.
5. **Granularity**: forecasts are assumed to be daily. Weekly/monthly forecasts need the
   actuals bucketed the same way.
6. **More metrics**: MASE needs the training series; quantile forecasts (pinball loss,
   coverage) need `quantile IS NOT NULL` rows.
7. **Long jobs**: there is no heartbeat, so `STALE_GRACE_SECONDS` must exceed the slowest job.
