"""services/scheduling/scheduler.py — generic async tick-loop scheduler,
ported verbatim from ND3X-public/src/services/scheduling/dynamic_scheduler.py.
Runs the lab TTL-reaper/reconcile and (Fase 5) the workflow-schedule cron tick.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from component_logging import get_logger

logger = logging.getLogger(__name__)
log = get_logger(__name__)

ScheduledTaskFn = Callable[[], Awaitable[None]]


@dataclass
class ScheduledTask:
    name: str
    interval_seconds: int
    fn: ScheduledTaskFn
    run_immediately: bool = True
    enabled: bool = True
    last_run_ts: float | None = None
    running: bool = False


class DynamicScheduler:
    """Small generic async scheduler for periodic internal tasks (lab TTL
    reaper, cron tick, ...). Does not replace a continuous queue worker — it
    only runs scheduled periodic functions."""

    def __init__(self, *, tick_seconds: int = 5):
        self.tick_seconds = tick_seconds
        self.tasks: dict[str, ScheduledTask] = {}
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None

    def register(self, *, name: str, interval_seconds: int, fn: ScheduledTaskFn,
                 run_immediately: bool = True, enabled: bool = True) -> None:
        if name in self.tasks:
            raise ValueError(f"Scheduled task already registered: {name}")
        self.tasks[name] = ScheduledTask(
            name=name, interval_seconds=interval_seconds, fn=fn,
            run_immediately=run_immediately, enabled=enabled, last_run_ts=None,
        )
        log.infox("Scheduler-taak geregistreerd", name=name,
                  interval_seconds=interval_seconds, run_immediately=run_immediately)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="labx-scheduler")
        log.infox("Scheduler gestart", tick_seconds=self.tick_seconds,
                  task_names=list(self.tasks.keys()))

    async def stop(self) -> None:
        if not self._task:
            return
        if self._stop_event:
            self._stop_event.set()
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self._stop_event = None
        log.infox("Scheduler gestopt")

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self.tick_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warningx("Scheduler-tick mislukt")
                logger.exception("Scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.tick_seconds)
            except asyncio.TimeoutError:
                pass

    async def tick_once(self) -> None:
        now = time.time()
        for task in list(self.tasks.values()):
            if not task.enabled or task.running:
                continue
            should_run = False
            if task.last_run_ts is None:
                if task.run_immediately:
                    should_run = True
                else:
                    task.last_run_ts = now
                    continue
            else:
                should_run = (now - task.last_run_ts) >= task.interval_seconds
            if should_run:
                await self._run_task(task)

    async def _run_task(self, task: ScheduledTask) -> None:
        task.running = True
        task.last_run_ts = time.time()
        try:
            await task.fn()
        except Exception as e:  # noqa: BLE001
            log.warningx("Scheduler-taak mislukt", name=task.name, error=repr(e))
            logger.exception("Scheduled task failed: %s", task.name)
        finally:
            task.running = False
