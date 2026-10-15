from dataclasses import dataclass
from enum import StrEnum


class Level(StrEnum):
    article = "article"
    """One row per (articleId, accountId)."""
    total = "total"
    """One pooled row over every evaluated point of the run."""


@dataclass(frozen=True)
class MetricRow:
    level: Level
    article_id: int | None
    account_id: int | None
    metric: str
    value: float | None
    """None = undefined for this slice (e.g. WAPE with zero actuals)."""
    n_points: int
