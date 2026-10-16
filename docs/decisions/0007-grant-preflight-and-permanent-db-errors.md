# 0007 — Grant preflight at start; permanent DB errors stop the process

**Status:** accepted · 2026-10-15 · extends the fault classes of
[ADR 0002](0002-jobs-from-data-jobs-with-conditional-claim.md)

## Context

The first run on dev logged, every few seconds and with growing back-off:

```
instance fault ((pymysql.err.OperationalError) (1142, "UPDATE command denied to user
'okremser'@'…' for table `circly_data_handling`.`data-jobs`")
```

pymysql raises a missing grant as `OperationalError`, the same class as a lost connection,
so ADR 0002's classification took it for an instance fault and retried forever. Waiting
never fixes a grant. And without a check at start, a missing right on the results table
would only surface after a job had been fully computed. AI-Pipeline-V3 hit the same
problem twice and answered it with a grant preflight (its ADR 0038).

## Decision

1. **The errno decides, not the exception class.** 1044, 1045, 1049, 1142, 1143 and 1146
   (no schema access, wrong credentials, unknown schema, missing table/column right,
   missing table) are **permanent** (`boundaries/db/driver_errors.py`).
2. **Permanent faults are a fourth fault class.** Not an instance fault (no back-off). The
   running job ends as `error_management` if that write still works, then the error
   escapes and `aim serve` exits 1.
3. **Grant preflight at start** (`boundaries/db/grants.py`): before the first poll, one
   probe per (table, right[, column]) that AIM's statements use. Each probe is one of four
   fixed `… WHERE 1=0` forms — MariaDB checks privileges before evaluating the predicate,
   so no row is touched. **All** gaps are collected and logged at once, as ready-to-run
   `GRANT` statements plus the missing tables; then exit 1. A connection error during the
   preflight is raised, not read as a grant verdict.
4. A unit test fails when a statement in `boundaries/db/` names a table without a probe,
   and when a probe deviates from its canonical form.

## Consequences

- A misconfigured environment fails in the first second with the full list of what the
  DBA has to do, instead of retrying silently or failing one job at a time.
- A new table, right or updated column in AIM's SQL needs its probe in the same commit
  (the table half is guarded; the right/column half is review discipline).
