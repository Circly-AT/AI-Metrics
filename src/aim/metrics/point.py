"""Point-forecast metrics. Every function takes (actual, forecast) as equal-length float
arrays and returns None when the metric is undefined for that input. Formulas match
AI-Pipeline-V3 (`aip.engine.evaluation.metrics`) so backtest and live numbers compare."""

import numpy as np


def wape(actual: np.ndarray, forecast: np.ndarray) -> float | None:
    """sum(|a - f|) / sum(|a|); None when there is no volume."""
    denom = float(np.abs(actual).sum())
    if actual.size == 0 or denom == 0:
        return None
    return float(np.abs(actual - forecast).sum()) / denom


def mae(actual: np.ndarray, forecast: np.ndarray) -> float | None:
    if actual.size == 0:
        return None
    return float(np.abs(actual - forecast).mean())


def rmse(actual: np.ndarray, forecast: np.ndarray) -> float | None:
    if actual.size == 0:
        return None
    return float(np.sqrt(((actual - forecast) ** 2).mean()))


def bias(actual: np.ndarray, forecast: np.ndarray) -> float | None:
    """sum(f - a) / sum(|a|): > 0 = over-forecast, < 0 = under-forecast."""
    denom = float(np.abs(actual).sum())
    if actual.size == 0 or denom == 0:
        return None
    return float((forecast - actual).sum()) / denom
