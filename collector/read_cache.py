"""Bounded single-flight cache: stale reads never wait for a database reconnect."""
import logging
import os
import threading
import time
from collections import OrderedDict


class ReadCache:
    def __init__(self, max_entries=32, workers=6, cooldown=1.5):
        self.entries = OrderedDict()
        self.pending = {}
        self.failures = {}
        self.errors = {}          # key -> 마지막 로드 실패 사유(진단용, 민감정보 없음)
        self.lock = threading.Lock()
        self.workers = workers
        self.slots = threading.BoundedSemaphore(workers)
        self.max_entries = max_entries
        # 실패 쿨다운은 프론트의 재시도(8회 ≈ 12초) 안에서 회복될 수 있게 짧게 둔다.
        # 너무 길면 한 번의 일시적 오류가 '영구 pending([])'으로 굳어버린다.
        self.cooldown = cooldown
        if hasattr(os, "register_at_fork"):
            os.register_at_fork(after_in_child=self._after_fork)

    def _after_fork(self):
        # A child inherits lock/event state, but none of the parent's running threads.
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(self.workers)
        self.pending = {}
        self.failures = {}

    def get(self, key, fetch, ttl=45, wait=None):
        """Return cached data or None while loading. wait=None loads cold keys inline.

        A stale value is served immediately while one bounded worker refreshes it.
        Setting wait=0 makes even a cold read nonblocking (location picker).
        """
        now = time.monotonic()
        with self.lock:
            hit = self.entries.get(key)
            if hit:
                self.entries.move_to_end(key)
                if now - hit[0] < ttl:
                    return hit[1]
            event = self.pending.get(key)
            launch = event is None and self.failures.get(key, 0) <= now
            if launch:
                if not self.slots.acquire(blocking=False):
                    return hit[1] if hit else None
                event = threading.Event()
                self.pending[key] = event
        if launch:
            if not hit and wait is None:
                self._refresh(key, fetch, event)
            else:
                threading.Thread(target=self._refresh, args=(key, fetch, event), daemon=True).start()
        if hit:
            return hit[1]
        if event:
            event.wait(5 if wait is None else wait)
        with self.lock:
            hit = self.entries.get(key)
            return hit[1] if hit else None

    def _refresh(self, key, fetch, event):
        try:
            value = fetch()
            with self.lock:
                # An invalidation during this query must not republish old data.
                if self.pending.get(key) is event:
                    self.entries[key] = (time.monotonic(), value)
                    self.entries.move_to_end(key)
                    self.failures.pop(key, None)
                    self.errors.pop(key, None)
                    while len(self.entries) > self.max_entries:
                        self.entries.popitem(last=False)
        except Exception as e:  # noqa: BLE001
            # 실제 예외 내용을 남긴다(이전엔 key만 찍혀 원인 파악이 불가능했다).
            logging.getLogger(__name__).warning(
                "Read cache refresh failed: %s -> %s: %s", key, type(e).__name__, e)
            with self.lock:
                if self.pending.get(key) is event:
                    self.failures[key] = time.monotonic() + self.cooldown
                    self.errors[key] = f"{type(e).__name__}: {e}"
                    while len(self.failures) > self.max_entries:
                        self.failures.pop(next(iter(self.failures)))
                    while len(self.errors) > self.max_entries:
                        self.errors.pop(next(iter(self.errors)))
        finally:
            with self.lock:
                if self.pending.get(key) is event:
                    self.pending.pop(key, None)
            self.slots.release()
            event.set()

    def invalidate(self, prefix=""):
        with self.lock:
            for mapping in (self.entries, self.pending, self.failures, self.errors):
                for key in list(mapping):
                    if key.startswith(prefix):
                        mapping.pop(key, None)

    def last_errors(self):
        """최근 로드 실패 사유 사본(진단용). {key: 'ErrType: msg'}."""
        with self.lock:
            return dict(self.errors)
