"""Pure metric functions plus the explicit registry. New metric = one function in
`point.py` + one entry in `METRICS`."""

from collections.abc import Callable, Mapping
from types import MappingProxyType

import numpy as np

from aim.metrics import point

Metric = Callable[[np.ndarray, np.ndarray], float | None]

METRICS: Mapping[str, Metric] = MappingProxyType(
    {
        "wape": point.wape,
        "mae": point.mae,
        "rmse": point.rmse,
        "bias": point.bias,
    }
)
