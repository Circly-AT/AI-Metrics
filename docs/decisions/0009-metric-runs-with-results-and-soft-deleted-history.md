# 0009 — Metric runs with results, history by soft delete

**Status:** accepted · 2026-10-27 · supersedes
[ADR 0005](0005-results-in-long-format-replaced-per-job.md) (the tables are still a
[proposal](../contracts/ai-metric-results.md), CIRBEFE-2077)

## Context

ADR 0005 wrote one long table: one row per (data-job, level, article, account, metric),
with window, tenant and forecast selection copied onto every row, `level = total` rows with
`NULL` article/account, and delete + insert on a re-run, so earlier numbers were lost.

The backend drafted two tables instead (CIRBEFE-2081, -2082): a **run** per metric that
references the backend's `metrics` master list and holds the pooled total, and **results**
per (run, article, account).

## Decision

1. **One run per (data-job, metric)** in `ai-metric-runs`. Its identity is
   (`cid`, `metricId`, `forecastSelection`, `windowStart`, `windowEnd`); `value` is the
   pooled total, `nPoints` the points behind it. Per-article values are
   `ai-metric-results` rows referencing the run. `level` disappears: a total belongs to
   the run, an article value to a result.
2. **A new metric is a new `metrics` row**, not DDL. AIM maps its names onto
   `metrics.name` (`METRIC_NAMES`: WAPE, MAE, RMSE, R_BIAS; AIM's bias is relative) and refuses to start while one
   of them has no row or more than one, printing the `INSERT`.
3. **History by soft delete.** A re-run sets `deleted_at` on the active run of the same
   identity and inserts a new one. The generated column `is_active` (1 while active, NULL
   once deleted) in the unique key makes "at most one active run per identity" a
   database guarantee: NULLs never collide, so any number of replaced runs can coexist
   with exactly one active run.
4. **Results are current through their run.** Result rows are never soft-deleted on their
   own; readers take results of runs with `deleted_at IS NULL`. Fewer writes and grants,
   and no second place that could disagree with the run.
5. **One transaction per data-job, plain INSERTs.** All metrics of a job become visible
   together. A crash rolls everything back, so the draft's `ON DUPLICATE KEY` upserts
   would never fire. They would also need `UPDATE` on `id` and `SELECT` on `version`, and
   they would not update `dataJobId`. They are dropped.
6. **Parallel writers: the transaction is retried.** Two data-jobs of the same identity
   can run at once (the claim only stops the same data-job from running twice). InnoDB
   then rolls one back with a deadlock (1213) or answers it with a duplicate key (1062).
   AIM runs that transaction again, up to 3 times. The retry deactivates the other run
   and becomes the newer one: last writer wins, never two active runs.
7. Proposed to the backend: `forecastSelection` `NOT NULL` (AIM always has one), which
   makes the generated `forecastSelectionKey` unnecessary. The backend kept both; for AIM's
   writes the two forms behave the same. Results carry no `cid` (the backend dropped it);
   the tenant comes through the run.

## Consequences

- Earlier numbers stay readable; "current" is a filter (`deleted_at IS NULL`) every
  reader must apply.
- A data-job writes four runs. Window, tenant and selection are stored once per run
  rather than per result row; `nPoints` repeats per metric because each metric is its
  own run.
- Grants: `SELECT` on `metrics`; `SELECT`, `UPDATE (deleted_at)` and a column `INSERT` on
  `ai-metric-runs`; a column `INSERT` on `ai-metric-results`. No `DELETE` any more.
- Still the backend's call, kept as drafted: `ON DELETE CASCADE` from `data-jobs`,
  `articles` and `accounts`. A **hard** delete of one of those takes its metric history
  with it.
