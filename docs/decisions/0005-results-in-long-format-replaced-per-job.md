# 0005 — Results in long format, replaced per data-job

**Status:** accepted · 2026-10-15 (the table itself is still a
[proposal](../contracts/ai-metric-results.md))

## Context

AIM writes per-article and total values for several metrics, and more metrics are planned
(MASE, quantile metrics). A data-job can be re-triggered and then runs again.

## Decision

- **Long format**: one row per (data-job, level, article, account, metric). A new metric
  is new rows, never a schema change.
- **Two levels**: `article` (one (article, account)) and `total`, which is **pooled** over
  every point of the job — not the mean of article values. For `wape` that equals the
  pipeline's `pooled_wape`; for `mae`/`rmse` it differs from the pipeline's `mean_*`
  ([formula contract](../contracts/metric-formulas.md#not-shared--same-name-or-not-do-not-compare)).
- **Undefined is `NULL`**, never 0 or NaN (e.g. WAPE of an article that sold nothing).
- **Delete + insert in one transaction per data-job**: a re-run replaces its rows, a reader
  never sees half a job or two runs mixed.

## Consequences

- Readers pivot when they want one column per metric.
- History across re-runs of the same data-job is not kept; a new data-job is a new history
  entry.
