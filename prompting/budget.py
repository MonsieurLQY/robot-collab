"""Finite request and wall-clock budgets for local Linux planning runners."""
from contextlib import contextmanager
import math
import signal
import threading
import time


class PlanningStopped(Exception):
    """A run exhausted a configured resource budget."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@contextmanager
def deadline(seconds, reason):
    """Interrupt blocking model/HTTP waits; preserve any enclosing deadline."""
    if seconds <= 0:
        raise PlanningStopped(reason)
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Planning deadlines require the main thread")
    started = time.monotonic()
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    enclosing_first = previous_delay > 0 and previous_delay <= seconds

    def expired(signum, frame):
        if enclosing_first and callable(previous_handler):
            previous_handler(signum, frame)
        raise PlanningStopped(reason)

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, min(seconds, previous_delay) if enclosing_first else seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_delay > 0:
            remaining = max(0.000001, previous_delay - (time.monotonic() - started))
            signal.setitimer(signal.ITIMER_REAL, remaining, previous_interval)


class PlanningBudget:
    def __init__(self, max_calls=30, max_seconds=600, request_seconds=60):
        if max_calls < 1 or any(not math.isfinite(v) or v <= 0
                                for v in (max_seconds, request_seconds)):
            raise ValueError("planning budgets must be finite and positive")
        self.max_calls = max_calls
        self.max_seconds = max_seconds
        self.request_seconds = request_seconds
        self.calls = 0
        self.started = time.monotonic()

    def remaining(self):
        return self.max_seconds - (time.monotonic() - self.started)

    def episode(self):
        return deadline(self.remaining(), "episode_timeout")

    @contextmanager
    def request(self):
        if self.calls >= self.max_calls:
            raise PlanningStopped("model_call_limit")
        remaining = self.remaining()
        if remaining <= 0:
            raise PlanningStopped("episode_timeout")
        self.calls += 1  # Count failed and timed-out requests as well.
        print(f"Model request {self.calls}/{self.max_calls}; "
              f"episode budget remaining {remaining:.0f}s", flush=True)
        timeout = min(self.request_seconds, remaining)
        reason = "episode_timeout" if remaining <= self.request_seconds else "model_request_timeout"
        with deadline(timeout, reason):
            yield
