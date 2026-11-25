"""Bounded sharing of in-flight preparation, without publishing documents.

Only callers still holding a session-store ticket may publish. Cancellation
releases a caller, not shared work: an already running job remains available to
another identical request and counts toward capacity until it really finishes.
Finished results live only until their current callers finish publication.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import threading

from backend.processing import ProcessingBusy


@dataclass
class _Preparation:
    task: asyncio.Task
    waiters: int = 0


class PreparationPool:
    def __init__(self, max_jobs=6):
        self.max_jobs = max_jobs
        self._pending = {}
        self._lock = threading.Lock()

    def _remove_if_finished(self, key, entry):
        if entry.waiters == 0 and entry.task.done() and self._pending.get(key) is entry:
            self._pending.pop(key)

    @asynccontextmanager
    async def acquire(self, identity, factory):
        loop = asyncio.get_running_loop()
        key = (loop, identity)
        with self._lock:
            entry = self._pending.get(key)
            # A retry need not inherit a completed failure while the original
            # HTTP callers are still unwinding their error responses.
            if entry and entry.task.done() and (entry.task.cancelled() or entry.task.exception() is not None):
                self._pending.pop(key)
                entry = None
            shared = entry is not None
            if entry is None:
                if len(self._pending) >= self.max_jobs:
                    raise ProcessingBusy('Document preparation is busy. Please retry shortly.')
                entry = _Preparation(loop.create_task(factory()))
                self._pending[key] = entry

                def finished(task):
                    # Consume failures even when every HTTP caller has left.
                    if not task.cancelled():
                        task.exception()
                    with self._lock:
                        self._remove_if_finished(key, entry)

                entry.task.add_done_callback(finished)
            entry.waiters += 1
        try:
            result = await asyncio.shield(entry.task)
            yield result, shared
        finally:
            with self._lock:
                entry.waiters -= 1
                self._remove_if_finished(key, entry)

    def stats(self):
        with self._lock:
            return {'jobs': len(self._pending), 'waiters': sum(e.waiters for e in self._pending.values()),
                    'max_jobs': self.max_jobs}
