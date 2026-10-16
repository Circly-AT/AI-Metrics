# Contract: metric formulas shared with AI-Pipeline-V3

**Status:** agreed (operator, 2026-10-15) for the metrics listed under *Shared*.
**Reference implementation:** AI-Pipeline-V3, `src/aip/engine/evaluation/metrics/`.
**Here:** `src/aim/metrics/point.py`, pinned by `tests/unit/test_metrics.py`.

## Promise

Where AIM and the pipeline compute a metric of the same name, they use the same formula
**on one target's series**, so a live number from AIM and a backtest number from the
pipeline compare directly. If either side changes a shared formula, the other side is told
first and follows in the same release, or the metric is renamed.

## Shared

`a` = actual, `f` = forecast (point), over the days of one target.

| Key | Formula | Undefined (AIM: `None`) when | Pipeline module |
|---|---|---|---|
| `wape` | `sum(\|a - f\|) / sum(\|a\|)` | `sum(\|a\|) == 0` | `wape.py` (same rule: `sum_actuals_zero`) |
| `mae` | `mean(\|a - f\|)` | no points | `mae.py` |
| `rmse` | `sqrt(mean((a - f)²))` | no points | `rmse.py` |

Pooled WAPE over many targets, `sum of all |a - f| / sum of all |a|`, is also shared: AIM's
`level = total` row of `wape` corresponds to the pipeline's `pooled_wape`.

## Not shared — same name or not, do not compare

| What | AIM | Pipeline |
|---|---|---|
| `bias` | `sum(f - a) / sum(\|a\|)` | not computed |
| `mase` | not computed (needs the training series) | `mase.py` |
| `baseline_gate` | not computed | `baseline_gate.py` |
| Target | one (article, account); with today's pipeline forecasts that is one article over all accounts | one article over all accounts (`AccountScope.all_accounts`, the only value; account level is post-MVP) |
| `total` of `mae` / `rmse` | **pooled** over all points | `mean_mae` / `mean_rmse` = **nanmean of per-target values** |
| No points | `None` | `nan` (never reached: the pipeline does not evaluate an empty target) |

## What the data behind the formulas means

The formulas match; the inputs do not have to. The pipeline evaluates **backtests** (a
hold-out split with its own lead time). AIM evaluates **live forecasts** chosen per day by a
strategy ([ADR 0004](../decisions/0004-forecast-selection-is-a-named-strategy.md)), without the
pipeline's return/special-sale filters
([reads contract](forecast-and-sales-reads.md)). Equal formulas make the numbers
*comparable*; they are not expected to be *equal*.

## How to add a shared metric

1. Agree on it with the pipeline side; note the date in the Status line above.
2. Implement it in `point.py` with the pipeline's formula and the same undefined condition.
3. Add it to `METRICS` in `src/aim/metrics/__init__.py`, a test with hand-computed values,
   and a row in the *Shared* table.
