"""Copy existing Python console output without changing its formatting."""
import logging
import sys
import threading
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Iterator, Union


class _TeeStream:
    def __init__(self, stream, log_file, lock):
        self.stream = stream
        self.log_file = log_file
        self.lock = lock

    def write(self, text):
        with self.lock:
            result = self.stream.write(text)
            self.log_file.write(text)
            self.log_file.flush()
            return result

    def flush(self):
        with self.lock:
            self.stream.flush()
            self.log_file.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


@contextmanager
def record_terminal(path: Union[str, Path]) -> Iterator[None]:
    """Record stdout, stderr and existing console logging during a run.

    Append when resuming a run. Preserve console streams and logging handlers
    on exit, including exceptions. Native file-descriptor writes are outside
    this Python stream recorder's scope.
    """
    with Path(path).open("a", encoding="utf-8") as log_file:
        stdout, stderr = sys.stdout, sys.stderr
        lock = threading.RLock()
        out = _TeeStream(stdout, log_file, lock)
        err = _TeeStream(stderr, log_file, lock)
        handlers = []
        loggers = [logging.getLogger()] + [
            logger for logger in logging.Logger.manager.loggerDict.values()
            if isinstance(logger, logging.Logger)
        ]
        seen = set()
        try:
            # basicConfig may have bound a handler before stream redirection.
            for logger in loggers:
                for handler in logger.handlers:
                    if id(handler) in seen:
                        continue
                    seen.add(id(handler))
                    if isinstance(handler, logging.StreamHandler):
                        stream = handler.stream
                        if stream is stdout or stream is stderr:
                            handlers.append((handler, stream))
                            handler.setStream(out if stream is stdout else err)
            with redirect_stdout(out), redirect_stderr(err):
                yield
        finally:
            for handler, stream in handlers:
                handler.setStream(stream)
