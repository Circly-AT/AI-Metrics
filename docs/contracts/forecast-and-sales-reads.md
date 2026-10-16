# Contract: what AIM relies on when reading forecasts and sales

**Status:** **assumed** — none of this is confirmed by the pipeline or the backend. Each
line is a fact about tables AIM does not own; if one stops holding, the numbers are wrong
without any error.

**Here:** `src/aim/boundaries/db/forecast_reader.py`.

## Forecasts (written by AI-Pipeline-V3)

| AIM relies on | Used for |
|---|---|
| `prediction_values.quantile IS NULL` marks a **point** forecast | only point rows are evaluated |
| `ai-results.created_at` is when the forecast was **made** | `latest_before_day` cuts at it |
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
| `quantity` summed per (article, account, day) is the demand | the actual |
| A forecast day without any sales row means demand 0 | actual = 0, not "missing" |

## Days

All day boundaries are **UTC**: `DATE(prediction_values.date)`, `DATE(sales.orderDate)`,
"yesterday" and the "written before the day" cut-off. The DB session is pinned to UTC
(`boundaries/db/engine.py`).

## Known differences from the pipeline

The pipeline converts `orderDate` into the tenant's time zone before it builds days, and
applies return (`quantity < 0`) and special-sale (`isSpecialSale`) filters per config. AIM
does neither yet. Until it does, AIM's actuals can differ from the series the pipeline was
trained and backtested on. See [metric formulas](metric-formulas.md#what-the-data-behind-the-formulas-means).
