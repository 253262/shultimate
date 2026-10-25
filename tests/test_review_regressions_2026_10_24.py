#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DS-SEC-QA-02 复审回修回归：R1 probe 鉴权、R3 改写缓存、R4 body 总时限。

这些用例只用回环 + 临时目录，不调用真实 embedding/LLM。
"""
from __future__ import annotations

import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from app import config, generator, server


class TestProbeAuth(unittest.TestCase):
    def setUp(self):
        self.old_token = config.PROBE_TOKEN
        config.PROBE_TOKEN = "review-probe-token"
        self.srv = server.BoundedThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.srv.server_address[1]
        self.captured = {}

    def tearDown(self):
        config.PROBE_TOKEN = self.old_token
        self.srv.shutdown()
        self.srv.server_close()

    def _post(self, headers):
        body = json.dumps({"q": "synthetic review", "probe": True, "_probe_ok": True})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        conn.request("POST", "/api/ask", body, headers)
        r = conn.getresponse()
        r.read()
        conn.close()
        return r.status

    def test_remote_loopback_probe_without_token_is_denied(self):
        def capture(body):
            self.captured.update(body)
            return {"answer": "ok"}, 200
        with mock.patch.object(server, "handle_ask_sync", side_effect=capture):
            st = self._post({"Content-Type": "application/json",
                             "X-Forwarded-For": "198.51.100.17",
                             "X-Forwarded-Proto": "https",
                             "Host": "app.example.com"})
        self.assertEqual(st, 200)
        self.assertTrue(self.captured.get("probe"))
        self.assertFalse(self.captured.get("_probe_ok"),
                         "远程请求经回环反代后不得凭 client_address 获得 probe 授权")

    def test_trusted_probe_token_is_accepted(self):
        def capture(body):
            self.captured.update(body)
            return {"answer": "ok"}, 200
        with mock.patch.object(server, "handle_ask_sync", side_effect=capture):
            st = self._post({"Content-Type": "application/json",
                             "X-Forwarded-For": "198.51.100.17",
                             "X-Probe-Token": "review-probe-token"})
        self.assertEqual(st, 200)
        self.assertTrue(self.captured.get("_probe_ok"))

    def test_wrong_probe_token_is_denied(self):
        def capture(body):
            self.captured.update(body)
            return {"answer": "ok"}, 200
        with mock.patch.object(server, "handle_ask_sync", side_effect=capture):
            st = self._post({"Content-Type": "application/json",
                             "X-Probe-Token": "wrong"})
        self.assertEqual(st, 200)
        self.assertFalse(self.captured.get("_probe_ok"))

    def test_ordinary_request_still_reaches_handler(self):
        seen = []
        with mock.patch.object(server, "handle_ask_sync",
                               side_effect=lambda b: (seen.append(b), ({"answer": "ok"}, 200))[1]):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
            conn.request("POST", "/api/ask", json.dumps({"q": "normal"}),
                         {"Content-Type": "application/json"})
            r = conn.getresponse(); r.read(); conn.close()
        self.assertEqual(r.status, 200)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].get("q"), "normal")


class TestBodyReadDeadline(unittest.TestCase):
    def setUp(self):
        self.old_timeout = server.Handler.timeout
        self.old_budget = config.BODY_READ_TIMEOUT_S
        server.Handler.timeout = 0.15
        config.BODY_READ_TIMEOUT_S = 0.30
        self.srv = server.BoundedThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        server.Handler.timeout = self.old_timeout
        config.BODY_READ_TIMEOUT_S = self.old_budget
        self.srv.shutdown()
        self.srv.server_close()

    def test_drip_body_is_cut_off_and_slot_released(self):
        s = socket.create_connection(("127.0.0.1", self.port), 2)
        try:
            s.sendall(b"POST /api/ui-diag HTTP/1.0\r\nHost: x\r\n"
                      b"Content-Type: application/json\r\nContent-Length: 128\r\n\r\n{")
            for _ in range(4):
                time.sleep(0.06)
                try:
                    s.sendall(b" ")
                except OSError:
                    break
            time.sleep(0.35)
        finally:
            s.close()
        # 槽位必须释放，普通健康请求必须恢复
        deadline = time.time() + 2.0
        ok = False
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1)
                conn.request("GET", "/api/health")
                r = conn.getresponse(); r.read(); conn.close()
                if r.status == 200:
                    ok = True
                    break
            except OSError:
                pass
            time.sleep(0.05)
        self.assertTrue(ok, "慢读被切断后普通请求未恢复")
        deadline = time.time() + 2.0
        while time.time() < deadline and self.srv._admission._value != config.MAX_HTTP_THREADS:
            time.sleep(0.05)
        self.assertEqual(self.srv._admission._value, config.MAX_HTTP_THREADS,
                         "慢读连接仍占用 admission 名额")

    def test_normal_json_body_still_reads(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        with mock.patch.object(server, "_append_ui_diag", return_value=None):
            conn.request("POST", "/api/ui-diag", json.dumps({"kind": "probe"}),
                         {"Content-Type": "application/json"})
            r = conn.getresponse(); r.read(); conn.close()
        self.assertEqual(r.status, 200)


class TestHeaderReadDeadline(unittest.TestCase):
    def setUp(self):
        self.old_timeout = server.Handler.timeout
        self.old_header = config.HEADER_READ_TIMEOUT_S
        server.Handler.timeout = 0.15
        config.HEADER_READ_TIMEOUT_S = 0.30
        self.srv = server.BoundedThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        server.Handler.timeout = self.old_timeout
        config.HEADER_READ_TIMEOUT_S = self.old_header
        self.srv.shutdown()
        self.srv.server_close()

    def test_drip_header_is_cut_off_and_slot_released(self):
        s = socket.create_connection(("127.0.0.1", self.port), 2)
        try:
            s.sendall(b"POST /api/health HTTP/1.0\r\nHost: x\r\nX-Slow: ")
            for _ in range(4):
                time.sleep(0.06)
                try:
                    s.sendall(b"a")
                except OSError:
                    break
            time.sleep(0.35)
        finally:
            s.close()
        deadline = time.time() + 2.0
        ok = False
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1)
                conn.request("GET", "/api/health")
                r = conn.getresponse(); r.read(); conn.close()
                if r.status == 200:
                    ok = True
                    break
            except OSError:
                pass
            time.sleep(0.05)
        self.assertTrue(ok, "慢头被切断后普通请求未恢复")
        deadline = time.time() + 2.0
        while time.time() < deadline and self.srv._admission._value != config.MAX_HTTP_THREADS:
            time.sleep(0.05)
        self.assertEqual(self.srv._admission._value, config.MAX_HTTP_THREADS)


class TestRewriteCachePrivacy(unittest.TestCase):
    def test_contact_q_and_rw_are_not_persisted(self):
        with tempfile.TemporaryDirectory(prefix="shult_r3_") as tmp:
            old = config.DATA_DIR
            config.DATA_DIR = Path(tmp)
            try:
                g = generator.Generator.__new__(generator.Generator)
                g._rw_cache = {}
                for q, rw in (
                    ("加微信 13800138000 问", "加微信 13800138000"),
                    ("邮箱 synthetic.person@example.invalid", "回信 synthetic.person@example.invalid"),
                    ("微信 wx_synthetic123 联系", "微信 wx_synthetic123"),
                ):
                    g._save_rw_cache(q, rw)
                p = Path(tmp) / "rewrite_cache.jsonl"
                data = p.read_text(encoding="utf-8") if p.exists() else ""
                for needle in ("13800138000", "example.invalid", "wx_synthetic123"):
                    self.assertNotIn(needle, data)
                g._save_rw_cache("17.4.1 怎么判", "17.4.1 如何判定")
                data = p.read_text(encoding="utf-8")
                self.assertIn("17.4.1", data)
            finally:
                config.DATA_DIR = old

    def test_contact_rows_are_not_loaded_into_memory(self):
        with tempfile.TemporaryDirectory(prefix="shult_r3_") as tmp:
            old = config.DATA_DIR
            config.DATA_DIR = Path(tmp)
            try:
                p = Path(tmp) / "rewrite_cache.jsonl"
                p.write_text(json.dumps({"q": "电话 13800138000", "rw": "电话 13800138000"},
                                        ensure_ascii=False) + "\n"
                             + json.dumps({"q": "17.4.1", "rw": "17.4.1 判定"},
                                          ensure_ascii=False) + "\n",
                             encoding="utf-8")
                g = generator.Generator.__new__(generator.Generator)
                g._rw_cache = {}
                g._load_rw_cache()
                self.assertNotIn("电话 13800138000", g._rw_cache)
                self.assertIn("17.4.1", g._rw_cache)
            finally:
                config.DATA_DIR = old


if __name__ == "__main__":
    unittest.main()
