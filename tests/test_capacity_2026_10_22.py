# -*- coding: utf-8 -*-
"""容量闸门守卫（2026-10-22，Task 3）。

钉住三件事：
  ① 超过上限时**排队超时**变成 Overloaded，而不是无限堆线程；
  ② 请求预算用尽时立刻失败（不是永远等）；
  ③ `/api/ask` 拿不到名额时返回 **429 + mode=overloaded + retry_after**，
     不是 500、不是空转。
"""
import http.server
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import capacity as C  # noqa: E402
from app import server  # noqa: E402


class TestGate(unittest.TestCase):
    def test_queue_timeout_and_release(self):
        g = C.Gate("测试", 1, 0.15)
        seen = {"overloaded": False}

        def worker():
            try:
                with g.slot():
                    pass
            except C.Overloaded:
                seen["overloaded"] = True

        with g.slot():
            t = threading.Thread(target=worker)
            t.start()
            t.join(2)
            self.assertTrue(t.is_alive() is False)
            self.assertTrue(seen["overloaded"], "第二个请求应该排队超时")
        # 释放后必须还能拿到 —— 不许出现名额泄漏
        with g.slot():
            pass

    def test_deadline_expiry_fails_fast(self):
        with C.request_budget(0.01):
            time.sleep(0.03)
            with self.assertRaises(C.Overloaded):
                with C.Gate("测试", 1, 1.0).slot():
                    pass

    def test_budget_timeout_and_check(self):
        with C.request_budget(0.02):
            self.assertLessEqual(C.timeout(10.0), 0.2)
            time.sleep(0.03)
            with self.assertRaises(C.Overloaded):
                C.check()


class _FakeSock:
    def __init__(self):
        self.data = b""
        self.closed = False

    def sendall(self, data):
        self.data += data

    def close(self):
        self.closed = True


class TestHTTPAdmission(unittest.TestCase):
    """2026-10-23 GPT 复核：线程总量必须在接入层有界。"""

    def test_extra_connection_gets_429_without_spawning_thread(self):
        srv = server.BoundedThreadingHTTPServer.__new__(
            server.BoundedThreadingHTTPServer)
        srv._admission = threading.BoundedSemaphore(1)
        with mock.patch.object(http.server.ThreadingHTTPServer,
                               "process_request", lambda *a, **k: None):
            srv.process_request(_FakeSock(), ("127.0.0.1", 1))
            second = _FakeSock()
            srv.process_request(second, ("127.0.0.1", 2))
        self.assertIn(b"429", second.data)
        self.assertIn(b"Retry-After: 5", second.data)
        self.assertIn(b"overloaded", second.data)
        self.assertTrue(second.closed)
        srv._admission.release()

    def test_worker_thread_releases_admission(self):
        srv = server.BoundedThreadingHTTPServer.__new__(
            server.BoundedThreadingHTTPServer)
        srv._admission = threading.BoundedSemaphore(1)
        srv._admission.acquire()
        with mock.patch.object(http.server.ThreadingHTTPServer,
                               "process_request_thread", lambda *a, **k: None):
            srv.process_request_thread(None, ("127.0.0.1", 3))
        self.assertTrue(srv._admission.acquire(blocking=False),
                        "线程结束后名额没释放")


class TestServerOverload(unittest.TestCase):
    def test_ask_returns_429_when_gate_full(self):
        class Boom:
            def slot(self):
                raise C.Overloaded("满载", 7)

        saved = C.REQUEST
        C.REQUEST = Boom()
        try:
            payload, code = server.handle_ask_sync({"q": "跨线防守"})
        finally:
            C.REQUEST = saved
        self.assertEqual(code, 429)
        self.assertEqual(payload.get("mode"), "overloaded")
        self.assertEqual(payload.get("retry_after"), 7)

    def test_stream_is_guarded_and_retry_after_exists(self):
        src = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
        self.assertIn("@_capacity_guard", src, "流式入口没有容量闸门")
        self.assertIn('"Retry-After"', src, "429 没带 Retry-After")


if __name__ == "__main__":
    unittest.main()
