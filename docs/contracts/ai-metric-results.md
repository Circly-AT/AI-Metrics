# Contract: `ai-metric-runs` and `ai-metric-results`

**Status:** live on dev since 2026-10-28 (CIRBEFE-2077), in the backend's form. DDL:
`ddl/ai-metric-runs.sql`; the design is
[ADR 0009](../decisions/0009-metric-runs-with-results-and-soft-deleted-history.md). Other
stages need the tables, the `metrics` rows and the grants (below).

**Here:** `src/aim/boundaries/db/metric_writer.py`.

## Shape

**`ai-metric-runs`**: one row per (data-job, metric):

| Column | Meaning |
|---|---|
| `dataJobId` | the `data-jobs` row that produced the run |
| `cid` | tenant |
| `metricId` | `metrics.id` of `WAPE`, `MAE`, `RMSE` or `R_BIAS` (AIM's relative bias; [formulas](metric-formulas.md)) |
| `forecastSelection` | strategy that chose the forecast per day: `latest_before_day`, or `ai_job:<ai-jobs.id>` for one ai-job's forecasts |
| `windowStart`, `windowEnd` | evaluated days, both inclusive (the tenant's local days) |
| `value` | the metric **pooled** over every point of the run; `NULL` = undefined, not 0 |
| `nPoints` | (article, account, day) points behind `value`, i.e. the sum of the results' `nPoints` |
| `deleted_at` | set when a later run of the same identity replaced this one |

**`ai-metric-results`**: one row per (run, article, account):

| Column | Meaning |
|---|---|
| `runId` | the run |
| `articleId`, `accountId` | the target; `accountId = 1` = all accounts (the pipeline's placeholder) |
| `value` | the metric for this target; `NULL` = undefined (e.g. WAPE of an article that sold nothing) |
| `nPoints` | days with a forecast for this target |

## Promises to readers

- **Current numbers are the results of active runs:** `JOIN ai-metric-runs r ON r.id =
  runId WHERE r.deleted_at IS NULL`. Result rows are never soft-deleted on their own.
- There is **at most one active run** per (`cid`, metric, `forecastSelection`, window);
  the database enforces it.
- Re-running a data-job, or a new data-job with the same identity, **replaces** the
  active run; the old run and its results stay as history.
- All runs of one data-job are written in **one transaction**: a reader sees all of them
  or none.
- A new metric is a new `metrics` row and new runs, never a new column.
- To compare runs, compare the same `forecastSelection` and look at `nPoints`; numbers
  from different strategies rest on different points.

## Grants AIM needs

The authoritative list is `REQUIRED` in `src/aim/boundaries/db/grants.py`; `aim serve`
checks it at start and prints the missing `GRANT` statements ([ADR 0007](../decisions/0007-grant-preflight-and-permanent-db-errors.md)).

| Table | Rights |
|---|---|
| `data-jobs` | SELECT, UPDATE (`state`, `containerID`, `error`) |
| `prediction_values`, `ai-results`, `ai-jobs`, `sales` | SELECT |
| `circly_customers.companies`, `circly_customers.timezones` | SELECT |
| `metrics` | SELECT |
| `ai-metric-runs` | SELECT, UPDATE (`deleted_at`), INSERT (the eight written columns) |
| `ai-metric-results` | INSERT (the five written columns) |
