"""The job queue (procrastinate on our Postgres). Run exactly one worker process (make worker): the LLM rate limiter
lives in that process, so one process keeps it truly global across both models."""

from __future__ import annotations

from procrastinate import App, PsycopgConnector
from sqlalchemy import make_url

from ..config import settings

QUEUES = ("intake", "read", "checks")


def _conninfo() -> str:
    return make_url(settings().database_url).set(drivername="postgresql").render_as_string(hide_password=False)


app = App(connector=PsycopgConnector(conninfo=_conninfo()), import_paths=["invoice_analyst.worker.tasks"])
