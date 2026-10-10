"""Start an inbox run: a workspace with the companion data, and every arrival scheduled at its simulated time.

Decision 32: arrivals are replayed in simulated time. Each document is scheduled `seconds_per_day` real seconds
per simulated day after the first arrival (0 = all at once); the worker (make worker) then receives, reads and
checks them, and the board fills as they arrive. The simulated clock is kept in the workspace settings.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select

from ..bench.runner import _git_sha
from ..companion import loader
from ..db.engine import session_factory
from ..db.models import Document, Invoice, Run, Workspace
from ..extraction import prompts
from ..worker.app import app
from ..worker.tasks import arrive

START_DELAY_S = 3.0


@dataclass
class Fed:
    run_id: int
    workspace_id: int
    documents: int
    first_date: dt.date
    last_date: dt.date
    seconds: float


async def feed(set_name: str, *, label: str | None = None, seconds_per_day: float = 2.0, fresh: bool = False,
               limit: int | None = None) -> Fed:
    stream = loader.stream(set_name)[:limit] if limit else loader.stream(set_name)
    first = dt.date.fromisoformat(stream[0]["received_date"])
    last = dt.date.fromisoformat(stream[-1]["received_date"])
    now = dt.datetime.now(dt.timezone.utc)
    start = now + dt.timedelta(seconds=START_DELAY_S)
    with session_factory().begin() as s:
        ws = Workspace(name=f"inbox-{set_name}-{now:%Y%m%dT%H%M%S}-{label or 'run'}", kind="inbox", dataset=set_name,
                       settings={"set": set_name, "cache_since": now.isoformat() if fresh else None,
                                 "clock": {"start_real": start.isoformat(), "start_sim": first.isoformat(),
                                           "seconds_per_day": seconds_per_day}})
        s.add(ws)
        s.flush()
        run = Run(workspace_id=ws.id, mode="inbox", git_sha=_git_sha(), status="running",
                  config={"set": set_name, "label": label, "documents": len(stream), "seconds_per_day": seconds_per_day,
                          "fresh": fresh, "prompt_hash": prompts.PROMPT_HASH})
        s.add(run)
        s.flush()
        loader.load(s, ws.id, set_name)
        ws_id, run_id = ws.id, run.id
    async with app.open_async():
        for k, item in enumerate(stream):
            days = (dt.date.fromisoformat(item["received_date"]) - first).days
            # same-day arrivals a moment apart, in stream order (the sequencer enforces the order regardless)
            at = start + dt.timedelta(seconds=days * seconds_per_day + k * 0.01) if seconds_per_day else None
            await arrive.configure(schedule_at=at).defer_async(
                workspace_id=ws_id, run_id=run_id, path=item["path"], ref=item["ref"], seq=item["seq"],
                received_date=item["received_date"])
    return Fed(run_id, ws_id, len(stream), first, last, (last - first).days * seconds_per_day)


def status(run_id: int) -> dict:
    """Progress of an inbox run: documents arrived and by state, invoices checked, finished or not."""
    with session_factory()() as s:
        run = s.get(Run, run_id)
        states = dict(s.execute(select(Document.state, func.count()).where(Document.workspace_id == run.workspace_id)
                                .group_by(Document.state)).all())
        inv_states = dict(s.execute(select(Invoice.state, func.count()).where(Invoice.workspace_id == run.workspace_id)
                                    .group_by(Invoice.state)).all())
        return {"run": run_id, "status": run.status, "expected": (run.config or {}).get("documents"),
                "arrived": sum(states.values()), "documents": states, "invoices": inv_states}
