# Contract: `data-jobs` as the job source

**Status:**
- Table and its DDL: **agreed** — it is the backend's existing table
  (`circly_data_handling.data-jobs`, identical to AI-Pipeline-V3's `ddl-snapshot/`).
- Which states AIM writes when, and the `jobConfiguration` schema: **assumed**, to be
  confirmed with the backend.

**Here:** `src/aim/boundaries/db/job_store.py`, `src/aim/kernel/jobs.py`.

## Which rows are AIM's

Rows with `type = 'metrics'` and `deleted_at IS NULL`. AIM never reads or writes
`type = 'abc_xyz'` rows. Order of work: `priority` ascending (smaller = more urgent, as in
`ai-jobs`), then `created_at`, then `id`.

## Who writes what

| Column | Written by | Value |
|---|---|---|
| `type`, `cid`, `priority`, `uid`, `jobConfiguration`, `version`, `created_at` | backend (insert) | — |
| `state` | backend: `trigger` on insert or re-trigger · AIM: everything after | see below |
| `containerID` | AIM, on claim | `<hostname>:<pid>` of the claiming instance |
| `error` | AIM | cleared on claim; reason on `error` / `error_management`, max. 2000 chars |
| `version` | — | AIM does not bump it (same as the pipeline on `ai-jobs`) |

## States

```
trigger ──claim──▶ job_detected ──▶ finished
                        ├─────────▶ error             metric computation failed
                        ├─────────▶ error_management  jobConfiguration unusable
                        └──stale──▶ error_management  owner stopped responding
```

- AIM only claims rows in `trigger`, and only ends rows in `job_detected` whose
  `containerID` is its own.
- **Stale:** a `job_detected` row unchanged for `STALE_GRACE_SECONDS` (default 3600) is
  ended as `error_management` by any instance.
- **No automatic retry**: the table has no attempts counter. Re-running a job = the backend
  sets `state` back to `trigger`; AIM then replaces that job's earlier results
  ([ADR 0005](../decisions/0005-results-in-long-format-replaced-per-job.md)).
- `error` vs. `error_management`: AIM's reading is "the job failed" vs. "the management
  side could not run it". **Assumed** — confirm what the frontend shows for each.

## `jobConfiguration`

JSON object, every key optional; empty / `NULL` / `{}` = all defaults. Parsed strictly: an
unknown key or a wrong type ends the job as `error_management` before any data is read.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `windowDays` | int, 1–366 | `30` | evaluate the last N complete days, ending yesterday (UTC) |
| `start`, `end` | `"YYYY-MM-DD"`, both or neither | — | fixed window, both inclusive; `end` must be before today; not together with `windowDays` |
| `forecastSelection` | string | `"latest_before_day"` | which forecast counts per day ([ADR 0004](../decisions/0004-forecast-selection-is-a-named-strategy.md)); currently the only value |

The backend writes no `jobConfiguration` today, so every job runs on the defaults, which
match the previous behaviour (last 30 days).
