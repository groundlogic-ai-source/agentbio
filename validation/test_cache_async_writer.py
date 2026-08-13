"""Tests for the asynchronous cache writer (cache/cache.py).

Root-cause regression guard for the 2026-08-12 prefetch wedge: a worker
holding the old process-wide write lock hung inside sqlite conn.close(),
freezing every network lane behind a timeout-less threading.Lock. Writes
are now enqueued to a single daemon writer thread; callers must never
block on SQLite, and read-your-write tests go through _flush_writes_for_test.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cache import cache  # noqa: E402


class TestAsyncCacheWriter(unittest.TestCase):

    def test_set_get_roundtrip_after_flush(self):
        cache.set("test-async-roundtrip", {"a": 1}, ttl_days=1)
        cache._flush_writes_for_test()
        self.assertEqual(cache.get("test-async-roundtrip"), {"a": 1})

    def test_overwrite_wins(self):
        cache.set("test-async-overwrite", "old", ttl_days=1)
        cache.set("test-async-overwrite", "new", ttl_days=1)
        cache._flush_writes_for_test()
        self.assertEqual(cache.get("test-async-overwrite"), "new")

    def test_expired_entry_reads_as_missing(self):
        cache.set("test-async-expired", "gone", ttl_days=-1)  # already expired
        cache._flush_writes_for_test()
        self.assertIsNone(cache.get("test-async-expired"))

    def test_concurrent_writers_never_block(self):
        # 8 threads x 100 writes must never wedge: in the healthy case the
        # inline write path serializes them on a bounded lock; under a stuck
        # holder they fall back to the queue after 2s. The bound is generous
        # (30s) — the assertion targets wedging, not micro-performance.
        def work(base: int):
            for i in range(100):
                cache.set(f"test-async-conc-{base}-{i}", i, ttl_days=1)
        threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertFalse(any(t.is_alive() for t in threads),
                         "set() blocked a worker thread")
        cache._flush_writes_for_test(timeout=60)
        self.assertEqual(cache.get("test-async-conc-3-42"), 42)


if __name__ == "__main__":
    unittest.main()
