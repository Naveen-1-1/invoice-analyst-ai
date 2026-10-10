"""Running the worker: first recover jobs a previous worker left half-done (e.g. it was stopped), then work."""

from __future__ import annotations

import inspect

from .app import QUEUES, app


async def retry_stalled() -> int:
    """Put back on the queue every job still marked 'doing' by a worker that is gone. Only one worker runs at a time
    (it holds the rate limiter), so at startup every such job is stalled."""
    jobs = list(await app.job_manager.get_stalled_jobs(seconds_since_heartbeat=0))
    for job in jobs:
        result = app.job_manager.retry_job(job)
        if inspect.isawaitable(result):
            await result
    return len(jobs)


async def main(concurrency: int = 8) -> None:
    async with app.open_async():
        recovered = await retry_stalled()
        print(f"Worker starting on queues {', '.join(QUEUES)} (concurrency {concurrency}); "
              f"{recovered} interrupted job(s) put back on the queue", flush=True)
        await app.run_worker_async(queues=list(QUEUES), concurrency=concurrency)
