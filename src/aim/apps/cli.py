"""Entry point `aim`.

    aim serve [--env-file .env] [--once]   poll data-jobs (type=metrics) until SIGTERM/SIGINT

Exit codes: 0 after a requested shutdown, 1 on any error -- including a failed grant
preflight at start (every missing grant/table listed at once) and a permanent DB error
(missing grant, wrong credentials) later on.
"""

import argparse
import logging
import os
import signal
import socket
import sys
import threading

from sqlalchemy.engine import Engine

from aim.boundaries.db.engine import create_db_engine, is_instance_fault, is_permanent_fault
from aim.boundaries.db.forecast_reader import DbForecastReader
from aim.boundaries.db.grants import check_grants
from aim.boundaries.db.job_store import DbJobStore
from aim.boundaries.db.metric_writer import DbMetricWriter
from aim.boundaries.settings import Settings
from aim.service.poller import Poller, PollerConfig

_LOGGER = logging.getLogger("aim")


def build_poller(settings: Settings, engine: Engine, stop: threading.Event) -> Poller:
    return Poller(
        jobs=DbJobStore(engine),
        reader=DbForecastReader(engine),
        writer=DbMetricWriter(engine),
        config=PollerConfig(
            owner=f"{socket.gethostname()}:{os.getpid()}",
            poll_interval=settings.poll_interval_seconds,
            stale_grace_seconds=settings.stale_grace_seconds,
        ),
        is_instance_fault=is_instance_fault,
        is_permanent_fault=is_permanent_fault,
        stop=stop,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aim")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="poll data-jobs and compute metrics")
    serve.add_argument("--env-file", default=None, help="read settings from this file")
    serve.add_argument("--once", action="store_true", help="a single poll, then exit")
    serve.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        settings = Settings(_env_file=args.env_file)
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: (_LOGGER.info("shutdown requested"), stop.set()))
        engine = create_db_engine(settings.db_url, tls_args=settings.db_tls_args)
        report = check_grants(engine)
        if not report.ok:
            _LOGGER.error(report.describe(user=settings.db_username, schema=settings.data_db))
            return 1
        poller = build_poller(settings, engine, stop)
        _LOGGER.info("aim serve started (grant preflight passed)")
        poller.serve(max_ticks=1 if args.once else None)
    except Exception:
        _LOGGER.exception("aim serve stopped on an error")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
