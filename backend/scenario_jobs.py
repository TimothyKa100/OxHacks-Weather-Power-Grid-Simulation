"""Bounded, in-process job runner for the demo (replaceable by an external queue).

One worker, at most two queued/running jobs, four retained jobs, 10-minute result
lifetime. Callers provide the computation; no Flask or simulator coupling here.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic
from uuid import uuid4


class JobCapacityError(Exception):
    pass


class ScenarioJobs:
    def __init__(self, max_pending=2, max_retained=4, ttl_seconds=600):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='storm-scenario')
        self.lock = Lock()
        self.jobs = {}
        self.max_pending, self.max_retained, self.ttl = max_pending, max_retained, ttl_seconds

    def _prune(self):
        now = monotonic()
        for key, job in list(self.jobs.items()):
            if job['status'] in ('complete', 'failed') and now - job['finished'] > self.ttl:
                del self.jobs[key]

    def submit(self, operation, cache_key=None):
        with self.lock:
            self._prune()
            if cache_key is not None:
                for key, job in self.jobs.items():
                    if job['cache_key'] == cache_key and job['status'] != 'failed':
                        return key
            pending = sum(j['status'] in ('queued', 'running') for j in self.jobs.values())
            if pending >= self.max_pending:
                raise JobCapacityError('Storm queue is full. Try again shortly.')
            while len(self.jobs) >= self.max_retained:
                oldest = next((k for k, j in self.jobs.items() if j['status'] in ('complete', 'failed')), None)
                if oldest is None:
                    raise JobCapacityError('Storm queue is full. Try again shortly.')
                del self.jobs[oldest]
            key = uuid4().hex
            self.jobs[key] = {'status': 'queued', 'cache_key': cache_key}
        self.executor.submit(self._run, key, operation)
        return key

    def _run(self, key, operation):
        with self.lock:
            self.jobs[key]['status'] = 'running'
        try:
            result = operation()
        except Exception:
            # Keep internal tracebacks in server logs, not public JSON.
            import logging
            logging.getLogger(__name__).exception('Storm scenario job failed')
            with self.lock:
                self.jobs[key].update(status='failed', error='Storm simulation failed. Check the server logs and retry.', finished=monotonic())
        else:
            with self.lock:
                self.jobs[key].update(status='complete', result=result, finished=monotonic())

    def get(self, key):
        with self.lock:
            self._prune()
            job = self.jobs.get(key)
            return {k: v for k, v in job.items() if k not in ('finished', 'cache_key')} if job else None
