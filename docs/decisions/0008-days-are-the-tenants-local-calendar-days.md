# 0008 — Days are the tenant's local calendar days

**Status:** accepted · 2026-10-16 · supersedes the "Days are UTC" point of
[ADR 0003](0003-a-job-is-a-tenant-and-a-window.md) and the "00:00 (UTC)" cut-off of
[ADR 0004](0004-forecast-selection-is-a-named-strategy.md)

## Context

ADR 0003 made days UTC provisionally. The pipeline does not count in UTC days. AI-Pipeline-V3
(its ADR 0021 addenda 19 and 21, `docs/system/boundaries/db/sales-read.md`) counts in the
tenant's **local calendar day**, using the zone in `circly_customers.companies.timezoneId → timezones.name`:

- it reads a sale's `orderDate` (a naive UTC instant) as the local day it falls on;
- it writes a forecast for local day D into `prediction_values.date` as **local midnight
  of D, in UTC**. In Vienna, 1 June is stored as `2026-05-31 22:00:00`.

AIM took `DATE(pv.date)` and `DATE(s.orderDate)`. Measured on dev on 2026-10-16:

- 22 M of 27 M point rows in the last 120 days sit at 22:00 or 23:00 UTC. AIM put each of
  those forecasts on the day **before** the one it was made for.
- Sales booked at local midnight shifted the same way, sales during the day did not. So
  forecast and actual met on the right day only by accident.
- `r.created_at < DATE(pv.date)` cut a full day early and dropped the forecast that
  `latest_before_day` is meant to pick.
- Four Vienna tenants have forecast rows at **00:00 UTC**, written as recently as
  2026-10-15. Something besides V3's `encode_product_day` writes `prediction_values` with
  another encoding. 00:00 UTC is 01:00 or 02:00 in Vienna, so it is still the same local day.

## Decision

1. **A day is the tenant's local calendar day** throughout: the window, "today" (the window
   still never includes it), the forecast's day, the sale's day and the
   `latest_before_day` cut-off.
2. **The zone comes from the tenant record, as in the pipeline.** The query is the same
   as V3's `_TIMEZONE_QUERY`: live `companies` row, live `timezones` row. A missing
   tenant, a missing or soft-deleted zone row, an empty name or a name polars cannot use
   raises `TenantTimezoneError`. The job ends as `error` with the reason. **It never falls
   back to UTC.**
3. **One zone database, polars**, through the pipeline's expressions ported into
   `boundaries/db/local_calendar_day.py`: UTC instant → local day, and local midnight →
   UTC instant. That includes the rules for a midnight the clocks skip (the day begins
   where the gap ends) or pass twice (the first). Not MariaDB's `CONVERT_TZ`: that would
   be a second zone database, which the pipeline ruled out.
4. **Days are ranges, not exact midnights.** Python computes every window day as a UTC
   range `[day_start, day_end)`, and the queries join it as a derived table: an instant
   belongs to the day whose range holds it. This covers both encodings seen in
   `prediction_values`, and a 23- or 25-hour day on a clock change. Ranking and summing
   stay in SQL. The cut-off becomes `r.created_at < d.day_start`.
5. **The zone stays at the DB boundary.** The `ForecastReader` port gains
   `local_today(cid, now)`; the service passes an aware UTC clock and never sees a zone.
6. The grant preflight probes `SELECT` on `circly_customers.companies` and `.timezones`.

## Consequences

- AIM and the pipeline put a sale and a forecast on the same day. Metrics written before
  this change are off by one day for most tenants and should be recomputed by re-triggering
  their data-jobs (ADR 0005 replaces the rows).
- `windowStart` / `windowEnd` in `ai-metric-results` are local days.
- One more small query per job (the zone, read twice: for "today" and for the load).
- Still open, unchanged: return / special-sale filters and day granularity (README).
- The writer of the 00:00-UTC forecast rows is unknown; worth asking the pipeline side.
  AIM does not depend on the answer.
