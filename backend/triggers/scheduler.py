"""Thin wrapper around a single process-wide APScheduler `BackgroundScheduler`
(spec-009 §4) -- thread-based, not asyncio-loop-based, consistent with this
codebase's existing "plain sync callables running in worker threads, never
nested inside an already-running event loop" rule (see backend/api/app.py's
module docstring and `run_graph`'s own internal `asyncio.run()`). A
schedule_trigger's cron tick fires `fire_fn` in one of the scheduler's own
worker threads, with no event loop of its own to conflict with.
"""

from __future__ import annotations

from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

_scheduler: BackgroundScheduler | None = None


def _get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
        _scheduler.start()
    return _scheduler


def _job_id(graph_id: str, node_id: str) -> str:
    return f"{graph_id}:{node_id}"


def add_schedule_job(graph_id: str, node_id: str, cron: str, fire_fn: Callable[[], None]) -> None:
    """`cron` is a standard 5-field crontab expression (spec-009 §5's
    `schedule_trigger` config shape). CronTrigger.from_crontab raises a plain
    ValueError on a malformed expression -- left to the caller (the
    /activate endpoint) to catch and turn into a clear 422, same pattern as
    every other config-validation failure in this codebase."""
    trigger = CronTrigger.from_crontab(cron)
    _get_scheduler().add_job(fire_fn, trigger=trigger, id=_job_id(graph_id, node_id))


def remove_jobs_for_graph(graph_id: str) -> None:
    scheduler = _get_scheduler()
    prefix = f"{graph_id}:"
    for job in scheduler.get_jobs():
        if job.id.startswith(prefix):
            scheduler.remove_job(job.id)


def get_jobs_for_graph(graph_id: str) -> list[str]:
    prefix = f"{graph_id}:"
    return [job.id for job in _get_scheduler().get_jobs() if job.id.startswith(prefix)]
