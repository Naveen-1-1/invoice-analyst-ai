"""A workspace's 'today' (decision 43).

A live inbox (arrivals replayed in simulated time) uses its simulated clock until the run finishes; then, and in
every other workspace, today is a date the user sets (as_of_date): by default the last arrival in an inbox, and
the real date in the manual workspace.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Document, Run, Workspace


@dataclass(frozen=True)
class Today:
    date: dt.date
    live: bool  # the simulated clock is running: today moves on its own and can't be set


def simulated(clock: dict, now: dt.datetime | None = None) -> dt.date:
    now = now or dt.datetime.now(dt.timezone.utc)
    start = dt.datetime.fromisoformat(clock["start_real"])
    days = max(0.0, (now - start).total_seconds()) / clock["seconds_per_day"]
    return dt.date.fromisoformat(clock["start_sim"]) + dt.timedelta(days=int(days))


def _live_clock(s: Session, ws: Workspace) -> dict | None:
    clock = (ws.settings or {}).get("clock")
    if not clock or not clock.get("seconds_per_day"):
        return None
    running = s.scalar(select(func.count()).select_from(Run).where(Run.workspace_id == ws.id,
                                                                   Run.status == "running"))
    return clock if running else None


def today(s: Session, ws: Workspace) -> Today:
    clock = _live_clock(s, ws)
    if clock:
        return Today(simulated(clock), True)
    if ws.as_of_date:
        return Today(ws.as_of_date, False)
    if ws.kind == "manual":
        return Today(dt.date.today(), False)
    last = s.scalar(select(func.max(Document.received_at)).where(Document.workspace_id == ws.id))
    return Today(last.date() if last else dt.date.today(), False)


def freeze(s: Session, ws: Workspace) -> None:
    """When an inbox run finishes: keep today where the clock (or the last arrival) left it, from now on settable."""
    if ws.as_of_date is None:
        ws.as_of_date = today(s, ws).date
