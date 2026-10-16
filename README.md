# AI-Metrics (`aim`)

Computes forecast accuracy metrics per tenant. A management service polls
`circly_data_handling.data-jobs` for `type = 'metrics'` rows, claims one, compares the
tenant's point forecasts over a window of past days (default: the last 30) with actual
sales and writes the metrics to `ai-metric-results`.

**Documentation: [`docs/`](docs/README.md)** — contracts (what is agreed with the pipeline
and the backend, and what is only assumed) and architectural decisions (ADRs).

## Run

```bash
uv sync
cp .env.example .env   # fill in DB_*, incl. DB_TLS + certificate paths (ADR 0006)
uv run aim serve --env-file .env          # poll until SIGTERM/SIGINT
uv run aim serve --env-file .env --once   # one poll, then exit
```

## Checks

```bash
uv run pytest          # unit tests (no DB needed)
uv run lint-imports    # layer contract (ADR 0001)
uv run ruff check
```

## Layout

```
src/aim/
  apps/        cli.py            entry point `aim serve`, wires everything together
  service/     poller.py         the loop: sweep stale → list trigger → claim → run → finish/fail
               runner.py         one job: parse config → window → load → evaluate → write
  boundaries/  settings.py       env reader (same DB_* names as the pipeline)
               db/job_store.py   data-jobs (type=metrics): conditional-UPDATE claim, sweep
               db/forecast_reader.py   forecast-selection strategies + sales → forecast vs. actual
               db/metric_writer.py     ai-metric-results
  metrics/     point.py          wape, mae, rmse, bias
               evaluate.py       per (article, account) + pooled total
  kernel/      jobs.py (states, jobConfiguration, window, ForecastSelection),
               results.py, ports.py (Protocols)
ddl/           proposed ai-metric-results table
docs/          contracts/ and decisions/
```

| Topic | Where |
|---|---|
| Job states, `jobConfiguration` keys and defaults | [contracts/data-jobs.md](docs/contracts/data-jobs.md) |
| Which formulas match the pipeline, which do not | [contracts/metric-formulas.md](docs/contracts/metric-formulas.md) |
| Which forecast counts for a day, how to add a strategy | [ADR 0004](docs/decisions/0004-forecast-selection-is-a-named-strategy.md) |
| Result table shape | [contracts/ai-metric-results.md](docs/contracts/ai-metric-results.md) |

## Open questions

1. **Forecast selection is untested against a DB**: the strategy queries run in SQL; the
   unit tests use fakes. Needs an integration test on a seeded MariaDB (ADR 0004).
2. **`error` vs. `error_management`**: our reading, to confirm with the backend
   ([data-jobs](docs/contracts/data-jobs.md#states)).
3. **Results table and grants**: a proposal ([ai-metric-results](docs/contracts/ai-metric-results.md)).
4. **Day boundaries and filters**: UTC days, no return/special-sale filters yet — numbers can
   drift from the pipeline's backtests ([reads](docs/contracts/forecast-and-sales-reads.md#known-differences-from-the-pipeline)).
5. **Granularity**: forecasts are assumed to be daily.
6. **More metrics**: MASE needs the training series; quantile forecasts (pinball loss,
   coverage) need `quantile IS NOT NULL` rows.
7. **Long jobs**: no heartbeat, so `STALE_GRACE_SECONDS` must exceed the slowest job (ADR 0002).
