"""AIM as a read-only MCP server: monitoring tools over what AIM computed.

    aim-mcp [--env-file .env]               stdio (the MCP client starts it)
    aim-mcp --env-file .env --http [--port]  streamable HTTP, for a long-running service

Read-only: every query is a SELECT (`aim.boundaries.db.metric_readout`); at start the
server checks the SELECT grants it needs (`READOUT_REQUIRED`) and stops with the missing
GRANT statements -- run it as a SELECT-only DB account, not with `aim serve`'s rights.

stdout carries the MCP protocol in stdio mode: nothing here may print to it (logs go to stderr).
"""

import argparse
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from aim.boundaries.db.engine import create_db_engine
from aim.boundaries.db.grants import READOUT_REQUIRED, check_grants
from aim.boundaries.db.metric_readout import DbMetricReadout
from aim.boundaries.settings import Settings
from aim.kernel.ports import MetricReadout
from aim.service import readout as answers

_LOGGER = logging.getLogger("aim.mcp")

INSTRUCTIONS = """\
Read-only view of AI-Metrics (AIM), which computes realized forecast accuracy per tenant
(customer, `cid`): it compares delivered point forecasts with actual sales over a window of
past days and stores pooled metrics -- wape, mae, rmse and bias (relative: sum(f-a)/sum|a|,
positive = over-forecast). Values are pooled over `n_points` (article, account, day) points;
null means undefined, not 0.

metric_status: is AIM itself working (job queue, failures, when each tenant last got numbers).
metric_summary: the latest numbers per tenant. Only compare numbers of the same `selection`
(default latest_before_day: the forecast that was live before each day began).
"""

mcp = MCPServer("aim", instructions=INSTRUCTIONS)
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

_STATE: dict[str, Any] = {"readout": None, "settings": None}


def set_readout(readout: MetricReadout) -> None:
    """Inject the readout (tests use an in-memory fake; `main` the DB one)."""
    _STATE["readout"] = readout


def _readout() -> MetricReadout:
    if _STATE["readout"] is None:
        raise ToolError("AIM readout is not connected (server started without a database)")
    return _STATE["readout"]


def _run(fn, **kwargs) -> dict[str, Any]:
    try:
        return fn(_readout(), now=datetime.now(UTC), **kwargs)
    except ToolError:
        raise
    except Exception as exc:  # DB down, grant revoked, ...: a readable tool error, no traceback
        _LOGGER.exception("tool failed")
        raise ToolError(f"AIM readout failed: {type(exc).__name__}: {exc}") from exc


@mcp.tool(annotations=READ_ONLY)
def metric_status(since_days: int = 7, failures_limit: int = 10) -> dict[str, Any]:
    """Health of the metric service itself.

    jobs.by_state: data-jobs (type=metrics) per state, updated within since_days.
    jobs.backlog: jobs waiting to be computed and how long the oldest has waited.
    jobs.claimed: jobs being computed; possibly_abandoned = no progress for over an hour.
    jobs.last_finished_at / minutes_since_last_finished: when AIM last completed a job.
    jobs.recent_failures: latest error / error_management jobs with their message.
    customers: per tenant when it last got metrics (hours_since_last_run), most overdue first.
    """
    if not 1 <= since_days <= 366:
        raise ToolError("since_days must be between 1 and 366")
    if not 1 <= failures_limit <= 100:
        raise ToolError("failures_limit must be between 1 and 100")
    return _run(answers.metric_status, since_days=since_days, failures_limit=failures_limit)


@mcp.tool(annotations=READ_ONLY)
def metric_summary(
    cid: str | None = None,
    since_days: int = 30,
    selection: str = answers.DEFAULT_SELECTION,
) -> dict[str, Any]:
    """Latest realized accuracy per tenant (or one tenant with cid).

    Per tenant: the evaluated window, when it was computed, and per metric (wape, mae, rmse,
    bias) the pooled value with its n_points. Only runs created within since_days count.
    selection: latest_before_day (default) or ai_job:<ai-jobs.id> for one forecast run.
    A metric carrying its own `window` comes from an older run than the tenant's window.
    """
    if not 1 <= since_days <= 366:
        raise ToolError("since_days must be between 1 and 366")
    return _run(answers.metric_summary, since_days=since_days, cid=cid or None, selection=selection)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="aim-mcp", description="AIM read-only MCP server (stdio by default)."
    )
    p.add_argument("--env-file", default=None, help="read DB_* settings from this file")
    p.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    p.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8010, help="HTTP port (default 8010)")
    p.add_argument("--log-level", default="WARNING")
    args = p.parse_args(argv)
    logging.basicConfig(  # stderr: stdout is the protocol in stdio mode
        stream=sys.stderr, level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = Settings(_env_file=args.env_file)
        engine = create_db_engine(settings.db_url, tls_args=settings.db_tls_args)
        report = check_grants(engine, READOUT_REQUIRED)
        if not report.ok:
            _LOGGER.error(report.describe(schema=settings.data_db))
            return 1
    except Exception:
        _LOGGER.exception("aim-mcp could not start")
        return 1
    set_readout(DbMetricReadout(engine))
    if args.http:
        mcp.run("streamable-http", host=args.host, port=args.port)
    else:
        mcp.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
