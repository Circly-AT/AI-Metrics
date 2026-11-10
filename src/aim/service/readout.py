"""Monitoring answers assembled from the `MetricReadout` port -- what `aim-mcp` returns.

Plain JSON-able dicts with a `schema_version`: readers (opsbot, an LLM agent) key on these
names, so a breaking change bumps the version. AIM reports what IS; whether something is
bad enough to alert on is the reader's call (thresholds live there, not here).
"""

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

from aim.kernel.jobs import JobState
from aim.kernel.ports import MetricReadout
from aim.kernel.readout import JobRef, RunRow

SCHEMA_VERSION = 1
DEFAULT_SELECTION = "latest_before_day"
MAX_ERROR_CHARS = 300


def _iso(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return value.isoformat()


def _minutes(now: datetime, then: datetime | None) -> int | None:
    return None if then is None else int((now - then).total_seconds() // 60)


def _clip(text: str | None) -> str | None:
    if text is None or len(text) <= MAX_ERROR_CHARS:
        return text
    return text[:MAX_ERROR_CHARS] + f" …[+{len(text) - MAX_ERROR_CHARS} chars]"


def _names(readout: MetricReadout, cids: Iterable[str]) -> dict[str, str]:
    unique = sorted(set(cids))
    return dict(readout.company_names(unique)) if unique else {}


def metric_status(
    readout: MetricReadout,
    *,
    now: datetime,
    since_days: int = 7,
    failures_limit: int = 10,
    stale_after_minutes: int = 60,
) -> dict[str, Any]:
    """Is AIM working? Job states over the last `since_days`, the queue, running and
    possibly abandoned claims, recent failures, and per tenant when it last got numbers."""
    since = now - timedelta(days=since_days)
    counts = {state.value: 0 for state in JobState}
    counts.update(readout.job_counts(since=since))
    waiting, oldest_waiting = readout.backlog()
    claimed = readout.claimed()
    last_finished = readout.last_finished_at()
    failures = readout.recent_failures(since=since, limit=failures_limit)
    activity = readout.customer_activity()
    names = _names(readout, [j.cid for j in (*claimed, *failures)] + [a.cid for a in activity])

    def job(j: JobRef, **extra: Any) -> dict[str, Any]:
        return {"id": j.id, "cid": j.cid, "company": names.get(j.cid), **extra}

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(now),
        "since_days": since_days,
        "jobs": {
            "by_state": counts,
            "backlog": {
                "waiting": waiting,
                "oldest_created_at": _iso(oldest_waiting),
                "oldest_age_minutes": _minutes(now, oldest_waiting),
            },
            "claimed": [
                job(
                    j,
                    owner=j.owner,
                    claimed_since=_iso(j.updated_at),
                    age_minutes=_minutes(now, j.updated_at),
                    possibly_abandoned=_minutes(now, j.updated_at) >= stale_after_minutes,
                )
                for j in claimed
            ],
            "last_finished_at": _iso(last_finished),
            "minutes_since_last_finished": _minutes(now, last_finished),
            "recent_failures": [
                job(j, state=j.state, at=_iso(j.updated_at), error=_clip(j.error)) for j in failures
            ],
        },
        "customers": sorted(
            (
                {
                    "cid": a.cid,
                    "company": names.get(a.cid),
                    "last_run_at": _iso(a.last_run_at),
                    "hours_since_last_run": round((now - a.last_run_at).total_seconds() / 3600, 1),
                    "last_window_end": _iso(a.last_window_end),
                }
                for a in activity
            ),
            key=lambda c: c["hours_since_last_run"],
            reverse=True,
        ),
    }


def metric_summary(
    readout: MetricReadout,
    *,
    now: datetime,
    since_days: int = 30,
    cid: str | None = None,
    selection: str = DEFAULT_SELECTION,
) -> dict[str, Any]:
    """Latest pooled numbers per tenant: per metric its latest active run of `selection`
    created within `since_days`. Compare tenants and runs only within one selection, and
    read `n_points` next to every value (docs/contracts/ai-metric-results.md)."""
    rows = readout.latest_runs(selection=selection, since=now - timedelta(days=since_days), cid=cid)
    by_cid: dict[str, list[RunRow]] = {}
    for r in rows:
        by_cid.setdefault(r.cid, []).append(r)
    names = _names(readout, by_cid)

    customers = []
    for tenant, runs in by_cid.items():
        newest = max(runs, key=lambda r: (r.window_end, r.created_at))
        window = (newest.window_start, newest.window_end)
        metrics: dict[str, Any] = {}
        for r in sorted(runs, key=lambda r: r.metric):
            entry: dict[str, Any] = {"value": r.value, "n_points": r.n_points}
            if (r.window_start, r.window_end) != window:  # a metric whose latest run is older
                entry["window"] = {"start": _iso(r.window_start), "end": _iso(r.window_end)}
            metrics[r.metric] = entry
        customers.append(
            {
                "cid": tenant,
                "company": names.get(tenant),
                "window": {"start": _iso(window[0]), "end": _iso(window[1])},
                "computed_at": _iso(max(r.created_at for r in runs)),
                "data_job_ids": sorted({r.data_job_id for r in runs}),
                "metrics": metrics,
            }
        )
    customers.sort(key=lambda c: ((c["company"] or "").lower(), c["cid"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(now),
        "selection": selection,
        "since_days": since_days,
        "customers": customers,
    }
