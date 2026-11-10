# 0004 — The forecast that counts for a day is a named strategy

**Status:** accepted · 2026-10-15 · the cut-off is local midnight since [ADR 0008](0008-days-are-the-tenants-local-calendar-days.md)

## Context

With a window ([ADR 0003](0003-a-job-is-a-tenant-and-a-window.md)) and regular forecast runs,
the same (article, account, day) is forecast several times — e.g. 7 days ahead, 3 days
ahead, the day before. One forecast per day has to be chosen. Options considered:

- **(a) latest before the day** — the most recent forecast written before the day began.
  Mixes lead times; the number closest to "how good was the forecast we had".
- **(b) fixed lead time** — e.g. exactly the forecast made 7 days before. Clean
  month-to-month comparison, matches order lead times; days without a run at that lead drop out.
- **(c) one result per lead time** — accuracy as a function of distance; needs a lead column.

## Decision

1. **Now: (a)**, named `latest_before_day`: per (article, account, day) the point value of
   the `ai-results` row with the latest `created_at` before 00:00 (UTC) of that day; ties go
   to the higher id. A forecast written on the day itself is never used.
2. **The choice is a named strategy, not a hard-coded rule**, so others can be added
   without touching the service:
   - `ForecastSelection` (`src/aim/kernel/jobs.py`) lists the strategies.
   - `jobConfiguration.forecastSelection` picks one; default `latest_before_day`.
   - `SELECTIONS` (`src/aim/boundaries/db/forecast_reader.py`) maps each strategy to its
     query. Every query takes `:cid, :start, :end_exclusive` and returns at most one
     `(articleId, accountId, day, forecast)` row per key. A unit test fails when a strategy
     has no query.
   - Every result row records its `forecastSelection`, so numbers from different
     strategies are never mixed up.

3. **Added 2026-11-10: `ai_job`** — the forecasts of one `ai-jobs` row named by
   `jobConfiguration.aiJobId`, without a `created_at` cut-off (several results of that job
   for a key: latest `created_at`, then higher id). The job must belong to the data-job's
   `cid`. Strategy and parameters travel together as `Selection` (`src/aim/kernel/jobs.py`):
   `Selection.params` are bound into the query, and `Selection.label` is what the run
   records as `forecastSelection` — `ai_job:<id>`, so two ai-jobs evaluated over the same
   window are separate run identities instead of replacing each other
   ([ADR 0009](0009-metric-runs-with-results-and-soft-deleted-history.md)).

## Adding a strategy

1. Member in `ForecastSelection`, with a docstring that states the rule exactly.
2. Query in `SELECTIONS` with the same parameters and the same output shape.
3. Row in the `forecastSelection` table of the [data-jobs contract](../contracts/data-jobs.md#jobconfiguration).
4. If the strategy needs its own parameter (e.g. `leadDays` for (b)), it becomes an optional
   field of `MetricJobConfig`, validated to appear only with its strategy, carried by
   `Selection` (its `params` and, if it is part of the run's identity, its `label`) and
   bound as a query parameter — as `aiJobId` for `ai_job`.
5. (c) is not a selection but a different result shape (several rows per day) — that needs
   a lead column in `ai-metric-results` and a new ADR.

## Consequences

- The rule lives in SQL (window function) and is **not covered by the unit tests**, which
  run against fakes. It needs an integration test on a seeded MariaDB: overlapping runs, a
  forecast written on the target day, a tie on `created_at`.
- Strategy names are part of the data-jobs contract once the backend writes them; renaming
  one is a contract change.
