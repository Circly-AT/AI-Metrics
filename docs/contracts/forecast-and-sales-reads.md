# Contract: what AIM relies on when reading forecasts and sales

**Status:** **assumed** — none of this is confirmed by the pipeline or the backend. Each
line is a fact about tables AIM does not own; if one stops holding, the numbers are wrong
without any error.

**Here:** `src/aim/boundaries/db/forecast_reader.py`.

## Forecasts (written by AI-Pipeline-V3)

| AIM relies on | Used for |
|---|---|
| `prediction_values.quantile IS NULL` marks a **point** forecast | only point rows are evaluated |
| `ai-results.created_at` is when the forecast was **made**, as a naive UTC instant | `latest_before_day` cuts at it |
| `prediction_values.date` is a naive UTC instant **inside** the local day forecast — the pipeline writes local midnight (`encode_product_day`); 00:00 UTC is also seen | the forecast's day |
| A tenant's results are reachable via `ai-results.aiJobId → ai-jobs.cid` | tenant filter (`ai-results` has no `cid`) |
| Forecasts are **daily**: at most one point value per result and day | one forecast per (article, account, day) |
| Backtests write no rows to `ai-results` / `prediction_values` | no backtest leaks into live metrics |
| Soft deletes are `deleted_at IS NOT NULL` on `ai-jobs`, `ai-results`, `prediction_values` | excluded |
| `ai-results.articleId` is set on rows to evaluate | rows without it are skipped |
| `ai-results.accountId = 1` means **all accounts** — the pipeline writes this placeholder (`aip.boundaries.db.prediction_rows.ACCOUNT_ID`, a dummy row seeded in every product DB, operator 2026-09-05) | such a forecast is compared with the article's sales **summed over all accounts**; any other `accountId` with that account's sales only |

## Actuals (`sales`, written by the backend's import)

| AIM relies on | Used for |
|---|---|
| `type = 'sales'` are the sales to forecast | other types (spoilage, transfers, …) are ignored |
| `orderDate` is a naive UTC instant | the sale's local day |
| `quantity` summed per (article, account, day) is the demand | the actual |
| A forecast day without any sales row means demand 0 | actual = 0, not "missing" |

## Days

A day is the tenant's **local calendar day** ([ADR 0008](../decisions/0008-days-are-the-tenants-local-calendar-days.md)): the zone is
`circly_customers.companies.timezoneId → timezones.name` (live rows only; anything unusable
fails the job, never falls back to UTC). Each window day becomes the UTC range
`[local midnight, next local midnight)`; a forecast, a sale and "today" belong to the day
whose range holds them, and `latest_before_day` takes results written before that range
starts. Conversion is polars only (`boundaries/db/local_calendar_day.py`, ported from
AI-Pipeline-V3). The DB session is pinned to UTC (`boundaries/db/engine.py`).

## Known differences from the pipeline

The pipeline applies return (`quantity < 0`) and special-sale (`isSpecialSale`) filters
per config. AIM does not yet. Until it does, AIM's actuals can differ from the series the pipeline was
trained and backtested on. See [metric formulas](metric-formulas.md#what-the-data-behind-the-formulas-means).
