import threading
import time
from typing import Any


class ThreadSafeMXCache:
    """Dict-like MX cache with internal locking.

    Implements the minimal dict protocol used by verifier.py
    (``__contains__``, ``__getitem__``, ``__setitem__``) so a single
    instance can be shared across worker threads.
    """

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._cache

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return self._cache[key]

    def __setitem__(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = value


class RateLimiter:
    """Per-host rate limiter using slot reservation.

    Each call to ``wait`` atomically reserves the next allowed timestamp
    for ``host``; concurrent callers serialize against the same host but
    never block each other across different hosts.
    """

    def __init__(self, delay: float) -> None:
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._delay = delay

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            scheduled = max(now, self._last.get(host, 0.0) + self._delay)
            self._last[host] = scheduled
            sleep_for = scheduled - now
        if sleep_for > 0:
            time.sleep(sleep_for)
