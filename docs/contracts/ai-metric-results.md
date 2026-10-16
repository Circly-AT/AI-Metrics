# Contract: `ai-metric-results`

**Status:** **proposal** — the table does not exist yet. DDL: `ddl/ai-metric-results.sql`.
Needs the backend's agreement and grants (below) before AIM can run in an environment.

**Here:** `src/aim/boundaries/db/metric_writer.py`.

## Shape

Long format, one row per (data-job, level, article, account, metric):

| Column | Meaning |
|---|---|
| `dataJobId` | the `data-jobs` row that produced the row |
| `cid` | tenant |
| `windowStart`, `windowEnd` | evaluated days, both inclusive (the tenant's local days) |
| `forecastSelection` | strategy that chose the forecast per day, e.g. `latest_before_day` |
| `level` | `article` = one (article, account) · `total` = pooled over every point of the job |
| `articleId`, `accountId` | set on `article`, `NULL` on `total` |
| `metric` | `wape`, `mae`, `rmse`, `bias` ([formulas](metric-formulas.md)) |
| `value` | `NULL` = undefined for this slice (e.g. WAPE with zero actuals), not 0 |
| `nPoints` | number of days behind the value |

## Promises to readers

- All rows of one data-job are written in **one transaction**: a reader sees all of them or
  none.
- Re-running a data-job **replaces** its rows; there are never rows from two runs of the
  same `dataJobId`.
- A new metric adds rows with a new `metric` value, never a new column.
- To compare two jobs, compare rows with the same `forecastSelection`; numbers from
  different strategies measure different things.

## Grants AIM needs

The authoritative list is `REQUIRED` in `src/aim/boundaries/db/grants.py`; `aim serve`
checks it at start and prints the missing `GRANT` statements ([ADR 0007](../decisions/0007-grant-preflight-and-permanent-db-errors.md)).

| Table | Rights |
|---|---|
| `data-jobs` | SELECT, UPDATE (`state`, `containerID`, `error`) |
| `prediction_values`, `ai-results`, `ai-jobs`, `sales` | SELECT |
| `ai-metric-results` | SELECT, INSERT, DELETE |
