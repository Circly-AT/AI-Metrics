"""Monitoring read side: the service answers against an in-memory readout, the SQL against
the reader contract (docs/contracts/ai-metric-results.md), and the MCP server end to end
over the in-memory MCP transport."""

import json
import re
from datetime import UTC, date, datetime, timedelta

import anyio
import pytest

from aim.boundaries.db import metric_readout as sql
from aim.boundaries.db.grants import CANONICAL_FORM, READOUT_REQUIRED
from aim.kernel.readout import CustomerActivity, JobRef, RunRow
from aim.service.readout import SCHEMA_VERSION, metric_status, metric_summary

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
CID_A = "c2d51869-c362-4567-a4e8-2400ba63e477"
CID_B = "9f1e0000-0000-4000-8000-000000000002"


def _job(id, cid, state, minutes_ago, error=None, owner=None):
    t = NOW - timedelta(minutes=minutes_ago)
    return JobRef(id=id, cid=cid, state=state, owner=owner, error=error, created_at=t, updated_at=t)


def _run(cid, metric, value, window_end=date(2026, 11, 9), job=7, hours_ago=3, n=1200):
    return RunRow(
        cid=cid, metric=metric, selection="latest_before_day",
        window_start=window_end - timedelta(days=29), window_end=window_end,
        value=value, n_points=n, data_job_id=job, created_at=NOW - timedelta(hours=hours_ago),
    )


class FakeReadout:
    def __init__(self, runs=(), claimed=(), failures=(), names=None):
        self.runs, self._claimed, self._failures = list(runs), tuple(claimed), tuple(failures)
        self.names = {CID_A: "Egger", CID_B: "Seeberger GmbH"} if names is None else names
        self.calls: list[tuple[str, dict]] = []

    def job_counts(self, *, since):
        self.calls.append(("job_counts", {"since": since}))
        return {"finished": 40, "error": 2}

    def backlog(self):
        return 3, NOW - timedelta(minutes=95)

    def claimed(self):
        return self._claimed

    def last_finished_at(self):
        return NOW - timedelta(minutes=20)

    def recent_failures(self, *, since, limit):
        return self._failures[:limit]

    def customer_activity(self):
        return (
            CustomerActivity(CID_A, NOW - timedelta(hours=5), date(2026, 11, 9)),
            CustomerActivity(CID_B, NOW - timedelta(hours=50), date(2026, 11, 7)),
        )

    def latest_runs(self, *, selection, since, cid):
        self.calls.append(("latest_runs", {"selection": selection, "since": since, "cid": cid}))
        return tuple(r for r in self.runs if cid in (None, r.cid))

    def company_names(self, cids):
        return {c: self.names[c] for c in cids if c in self.names}


# --- metric_status ---------------------------------------------------------------------

def test_status_reports_queue_claims_failures_and_overdue_tenants():
    readout = FakeReadout(
        claimed=[_job(11, CID_A, "job_detected", 5, owner="host:1"),
                 _job(12, CID_B, "job_detected", 90, owner="host:2")],
        failures=[_job(13, CID_B, "error", 30, error="no forecast in the window " + "x" * 400)],
    )
    out = metric_status(readout, now=NOW, since_days=7)

    assert out["schema_version"] == SCHEMA_VERSION
    assert readout.calls[0] == ("job_counts", {"since": NOW - timedelta(days=7)})
    jobs = out["jobs"]
    # every state present, missing ones as 0
    assert jobs["by_state"] == {
        "trigger": 0, "job_detected": 0, "finished": 40, "error": 2, "error_management": 0,
    }
    assert jobs["backlog"] == {
        "waiting": 3, "oldest_created_at": "2026-11-10T10:25:00Z", "oldest_age_minutes": 95,
    }
    claims = [(c["id"], c["possibly_abandoned"]) for c in jobs["claimed"]]
    assert claims == [(11, False), (12, True)]
    assert jobs["minutes_since_last_finished"] == 20
    failure = jobs["recent_failures"][0]
    assert (failure["company"], failure["state"]) == ("Seeberger GmbH", "error")
    assert failure["error"].endswith("chars]") and len(failure["error"]) < 330
    # most overdue tenant first
    assert [(c["company"], c["hours_since_last_run"]) for c in out["customers"]] == [
        ("Seeberger GmbH", 50.0), ("Egger", 5.0),
    ]
    json.dumps(out)  # JSON-able as-is


# --- metric_summary --------------------------------------------------------------------

def test_summary_groups_metrics_per_tenant():
    readout = FakeReadout(runs=[
        _run(CID_A, "wape", 0.31), _run(CID_A, "bias", 0.05), _run(CID_A, "mae", 4.2),
        _run(CID_B, "wape", None, n=0),  # undefined stays None, never 0
    ])
    out = metric_summary(readout, now=NOW)

    assert out["selection"] == "latest_before_day"
    assert [c["company"] for c in out["customers"]] == ["Egger", "Seeberger GmbH"]
    egger = out["customers"][0]
    assert egger["window"] == {"start": "2026-10-11", "end": "2026-11-09"}
    assert egger["metrics"] == {
        "bias": {"value": 0.05, "n_points": 1200},
        "mae": {"value": 4.2, "n_points": 1200},
        "wape": {"value": 0.31, "n_points": 1200},
    }
    assert out["customers"][1]["metrics"]["wape"] == {"value": None, "n_points": 0}
    json.dumps(out)


def test_summary_marks_a_metric_from_an_older_window():
    readout = FakeReadout(runs=[
        _run(CID_A, "wape", 0.31, window_end=date(2026, 11, 9)),
        _run(CID_A, "rmse", 9.0, window_end=date(2026, 11, 2), job=5, hours_ago=170),
    ])
    egger = metric_summary(readout, now=NOW)["customers"][0]
    assert egger["window"]["end"] == "2026-11-09"
    assert "window" not in egger["metrics"]["wape"]
    assert egger["metrics"]["rmse"]["window"] == {"start": "2026-10-04", "end": "2026-11-02"}
    assert egger["data_job_ids"] == [5, 7]


def test_summary_passes_filters_through():
    readout = FakeReadout(runs=[_run(CID_A, "wape", 0.3)])
    metric_summary(readout, now=NOW, since_days=14, cid=CID_A, selection="ai_job:80593")
    assert readout.calls[-1] == (
        "latest_runs",
        {"selection": "ai_job:80593", "since": NOW - timedelta(days=14), "cid": CID_A},
    )


def test_unknown_company_is_null_not_missing():
    readout = FakeReadout(runs=[_run(CID_A, "wape", 0.3)], names={})
    assert metric_summary(readout, now=NOW)["customers"][0]["company"] is None


# --- SQL against the reader contract ---------------------------------------------------

def _statements():
    return [v for k, v in vars(sql).items() if k.isupper() and isinstance(v, str) and "SELECT" in v]


def test_readout_only_selects():
    for statement in _statements():
        assert statement.lstrip().upper().startswith("SELECT"), statement
        # word boundaries: the column `updated_at` is not an UPDATE
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", statement.upper()), statement


def test_runs_are_read_from_active_rows_only():
    # contract: current numbers = runs with deleted_at IS NULL, compared within one selection
    for statement in (sql.LATEST_RUNS, sql.CUSTOMER_ACTIVITY):
        assert "deleted_at IS NULL" in statement
    assert "forecastSelection = :selection" in sql.LATEST_RUNS


def test_readout_grants_are_select_only_and_cover_every_table():
    assert {p.right for p in READOUT_REQUIRED} == {"SELECT"}
    probed = {p.table for p in READOUT_REQUIRED}
    for table in ("data-jobs", "ai-metric-runs", "metrics", "companies"):
        assert table in probed
    for p in READOUT_REQUIRED:
        assert p.statement == CANONICAL_FORM["SELECT"].format(ref=p.ref)


def test_metric_names_map_back_to_aim_keys():
    assert sql._METRIC_KEYS == {"WAPE": "wape", "MAE": "mae", "RMSE": "rmse", "R_BIAS": "bias"}


# --- MCP server end to end (in-memory transport) ---------------------------------------

@pytest.fixture
def server():
    from aim.apps import mcp_server

    mcp_server.set_readout(FakeReadout(runs=[_run(CID_A, "wape", 0.31)]))
    yield mcp_server
    mcp_server.set_readout(None)


def test_mcp_tools_are_read_only_and_answer(server):
    from mcp import Client

    async def run():
        async with Client(server.mcp) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert set(tools) == {"metric_status", "metric_summary"}
            assert all(t.annotations.read_only_hint for t in tools.values())

            summary = (await client.call_tool("metric_summary", {})).structured_content
            assert summary["customers"][0]["metrics"]["wape"]["value"] == 0.31

            status = (await client.call_tool("metric_status", {"since_days": 3})).structured_content
            assert status["since_days"] == 3

            bad = await client.call_tool("metric_summary", {"since_days": 0})
            assert bad.is_error

    anyio.run(run)


def test_mcp_without_readout_is_a_tool_error(server):
    from mcp import Client

    server.set_readout(None)

    async def run():
        async with Client(server.mcp) as client:
            result = await client.call_tool("metric_status", {})
            assert result.is_error
            assert "not connected" in result.content[0].text

    anyio.run(run)
