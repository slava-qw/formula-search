"""Small reusable JSON worker pool with bounded leases and hard retirement.

Capacity covers startup, requests and kill/reap, including idle expiry. Pipe
writes run in the exchange thread too: Windows pipes may fill during imports.
Only one request is ever in flight on a worker. Invalid output retires it.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid


class _Worker:
    def __init__(self, command, cwd):
        self.process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.requests = 0
        self.generation = 0
        self.state = 'leased'
        self.timer = None
        self.thread = None
        self.close_lock = threading.Lock()
        self.closed = False

    def close(self):
        with self.close_lock:
            if self.closed:
                return
            if self.timer:
                self.timer.cancel()
            if self.process.poll() is None:
                self.process.kill()
            # Never release capacity before the child has actually exited.
            self.process.wait()
            thread = self.thread
            if thread and thread.ident is not None:
                thread.join()
            # A killed child can leave buffered input unwritten. TextIO.close
            # still closes its underlying handle, but its final flush can fail
            # with EINVAL/BrokenPipe on Windows. The child is already reaped.
            for stream in (self.process.stdin, self.process.stdout):
                try:
                    stream.close()
                except OSError:
                    pass
            self.closed = True


class ScalarWorkerPool:
    def __init__(self, command, cwd, *, max_workers=2, max_requests=32, idle_seconds=60):
        self.command, self.cwd = command, cwd
        self.max_requests, self.idle_seconds = max_requests, idle_seconds
        self._slots = threading.BoundedSemaphore(max_workers)
        self._lock = threading.RLock()
        self._workers = set()
        self._idle = []
        self._closed = False

    def _retire(self, worker):
        with self._lock:
            worker.state = 'retiring'
            if worker in self._idle:
                self._idle.remove(worker)
            if worker.timer:
                worker.timer.cancel()
        worker.close()
        with self._lock:
            self._workers.discard(worker)

    def _timer(self, worker, generation, delay):
        timer = threading.Timer(delay, self._expire, args=(worker, generation))
        timer.daemon = True
        worker.timer = timer
        timer.start()

    def _expire(self, worker, generation):
        if not self._slots.acquire(blocking=False):
            with self._lock:
                if not self._closed and worker.state == 'idle' and worker.generation == generation:
                    try:
                        self._timer(worker, generation, .1)
                    except RuntimeError:
                        # No timer thread can be started. This worker is idle;
                        # holding the pool lock through reap prevents replacement
                        # startup from overlapping it, even with reserved slots.
                        self._retire(worker)
            return
        try:
            with self._lock:
                if self._closed or worker.state != 'idle' or worker.generation != generation:
                    return
                self._idle.remove(worker)
                worker.state = 'retiring'
            self._retire(worker)
        finally:
            self._slots.release()

    @staticmethod
    def _validate(response, request_id, payload):
        if not isinstance(response, dict) or response.get('id') != request_id:
            raise ValueError('Mismatched scalar response')
        result = response.get('result')
        if not isinstance(result, dict) or result.get('status') not in ('complete', 'limited', 'unsupported'):
            raise ValueError('Invalid scalar status')
        indices = result.get('indices')
        eligible = {item[0] for item in payload['candidates']}
        if (not isinstance(indices, list) or any(type(index) is not int or index not in eligible for index in indices)
                or len(indices) != len(set(indices))):
            raise ValueError('Invalid scalar indices')
        return {'indices': indices, 'status': result['status']}

    def request(self, payload, timeout):
        if not self._slots.acquire(blocking=False):
            return {'indices': [], 'status': 'busy'}
        deadline = time.monotonic()+timeout
        worker = None
        reusable = False
        try:
            with self._lock:
                if self._closed:
                    return {'indices': [], 'status': 'unavailable'}
                if self._idle:
                    worker = self._idle.pop()
                    worker.state = 'leased'
                    worker.generation += 1
                    worker.timer.cancel()
            if worker and worker.process.poll() is not None:
                self._retire(worker)
                worker = None
            if worker is None:
                with self._lock:
                    if self._closed:
                        return {'indices': [], 'status': 'unavailable'}
                    worker = _Worker(self.command, self.cwd)
                    self._workers.add(worker)
            request_id = uuid.uuid4().hex
            output, done = {}, threading.Event()
            def exchange():
                try:
                    worker.process.stdin.write(json.dumps({'id': request_id, **payload})+'\n')
                    worker.process.stdin.flush()
                    line = worker.process.stdout.readline(65537)
                    if len(line) > 65536 or not line.endswith('\n'):
                        raise ValueError('Invalid scalar response frame')
                    output['result'] = self._validate(json.loads(line), request_id, payload)
                except (OSError, ValueError, TypeError, KeyError) as error:
                    output['error'] = error
                finally:
                    done.set()
            with self._lock:
                if self._closed or worker.state != 'leased':
                    return {'indices': [], 'status': 'unavailable'}
                worker.thread = threading.Thread(target=exchange, daemon=True, name='formula-scalar-exchange')
                worker.thread.start()
            if not done.wait(max(0, deadline-time.monotonic())) or time.monotonic() > deadline:
                return {'indices': [], 'status': 'timeout'}
            if 'result' not in output:
                return {'indices': [], 'status': 'unavailable'}
            worker.thread.join()
            worker.thread = None
            worker.requests += 1
            reusable = worker.requests < self.max_requests and worker.process.poll() is None
            return output['result']
        except (OSError, ValueError, RuntimeError):
            return {'indices': [], 'status': 'unavailable'}
        finally:
            try:
                if worker:
                    with self._lock:
                        keep = reusable and not self._closed and worker.state == 'leased'
                        if keep:
                            worker.state = 'idle'
                            worker.generation += 1
                            self._idle.append(worker)
                            try:
                                self._timer(worker, worker.generation, self.idle_seconds)
                            except RuntimeError:
                                keep = False  # retire while this lease still owns capacity
                    if not keep:
                        self._retire(worker)
            finally:
                self._slots.release()

    def close(self):
        with self._lock:
            self._closed = True
            workers = list(self._workers)
            self._idle.clear()
            for worker in workers:
                worker.state = 'retiring'
                if worker.timer:
                    worker.timer.cancel()
        for worker in workers:
            self._retire(worker)

    def snapshot(self):
        """Read-only lifecycle diagnostics, also used by process-level tests."""
        with self._lock:
            return {'closed': self._closed, 'workers': [
                {'pid': w.process.pid, 'state': w.state, 'requests': w.requests,
                 'returncode': w.process.poll()} for w in self._workers]}
