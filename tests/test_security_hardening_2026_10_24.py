# -*- coding: utf-8 -*-
"""DS-SEC-QA-01 服务端安全回归（2026-10-24）。

覆盖已确认缺陷：
  SEC-20261024-010  Content-Length 负数绕过 MAX_BODY / Transfer-Encoding
  SEC-20261024-011  /api/ui-diag 任意字段落盘（嵌套 q、UA、会话 id）
  SEC-20261024-012  /api/feedback 公开 recent 含他人 q/comment
  SEC-20261024-013  /api/search、/api/branches、/api/practice 无请求闸门
  SEC-20261024-014  top_k=inf 触发 500；非 ASCII Owner Token 触发 500
  SEC-20261024-015  /api/practice 下发 explain/why/evidence（答案）
  SEC-20261024-016  /api/curated stats 返回服务器绝对路径
  SEC-20261024-017  每日上游调用无总预算

全部零网络、临时目录；不碰真实 data/。
"""
from __future__ import annotations

import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import capacity as C          # noqa: E402
from app import config                 # noqa: E402
from app import server                 # noqa: E402


class _Headers:
    def __init__(self, items):
        self.items = list(items)

    def get(self, key, default=None):
        for k, v in self.items:
            if k.lower() == key.lower():
                return v
        return default

    def get_all(self, key):
        return [v for k, v in self.items if k.lower() == key.lower()]


class _FakeHandler:
    def __init__(self, body=b"", headers=None, addr=("127.0.0.1", 1)):
        self.headers = _Headers(headers or [])
        self.rfile = io.BytesIO(body)
        self.close_connection = False
        self.client_address = addr


def _read(headers, body=b""):
    h = _FakeHandler(body=body, headers=headers)
    return server.Handler._read_body(h), h


class TestBodyBoundaries(unittest.TestCase):
    def test_handler_has_socket_timeout(self):
        """24 个半截 body 连接不能永久占满线程。"""
        self.assertGreater(server.Handler.timeout, 0)

    def test_security_headers_present_on_json(self):
        import http.client
        import threading
        srv = server.BoundedThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            port = srv.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/health")
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
            self.assertIn("frame-ancestors 'none'",
                          resp.getheader("Content-Security-Policy") or "")
            self.assertEqual(resp.getheader("X-Frame-Options"), "DENY")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_negative_content_length_rejected_before_read(self):
        got, h = _read([("Content-Length", "-1")], b"secret")
        self.assertIn("__bad_body__", got)
        self.assertTrue(h.close_connection)

    def test_transfer_encoding_rejected(self):
        got, _ = _read([("Transfer-Encoding", "chunked")], b"0\r\n\r\n")
        self.assertIn("__bad_body__", got)

    def test_duplicate_content_length_rejected(self):
        got, _ = _read([("Content-Length", "2"), ("Content-Length", "3")], b"{}")
        self.assertIn("__bad_body__", got)

    def test_invalid_content_length_rejected(self):
        got, _ = _read([("Content-Length", "abc")], b"{}")
        self.assertIn("__bad_body__", got)

    def test_too_large_is_flagged(self):
        got, h = _read([("Content-Length", str(server.MAX_BODY + 1))])
        self.assertTrue(got.get("__too_big__"))
        self.assertTrue(h.close_connection)

    def test_normal_json_ok(self):
        got, _ = _read([("Content-Length", "7")], b'{"q":1}')
        self.assertEqual(got, {"q": 1})


class TestUiDiagAllowlist(unittest.TestCase):
    def test_nested_q_ua_and_session_ids_are_dropped(self):
        raw = {
            "kind": "ui-stuck", "url": "/#/chat", "ua_short": "Mozilla/5.0 secret",
            "askedSid": "session-123", "curId": "session-456",
            "d": {"q": "我的真名张三 13800000000"},
            "strays": 2, "stillHere": True, "msgs": 3,
        }
        clean = server._sanitize_ui_diag(raw)
        self.assertEqual(clean["kind"], "ui-stuck")
        self.assertEqual(clean["strays"], 2)
        self.assertTrue(clean["stillHere"])
        for key in ("ua_short", "askedSid", "curId", "d", "q"):
            self.assertNotIn(key, clean)

    def test_unknown_types_dropped(self):
        clean = server._sanitize_ui_diag({"kind": {"x": 1}, "strays": "2",
                                          "stillHere": "yes", "__proto__": {"q": "x"}})
        self.assertEqual(clean["kind"], "ui-stuck")
        self.assertNotIn("strays", clean)
        self.assertNotIn("stillHere", clean)
        self.assertNotIn("__proto__", clean)

    def test_append_is_bounded_and_has_no_q_ua(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(config, "DATA_DIR", pathlib.Path(d)):
                with mock.patch.object(config, "UI_DIAG_MAX_BYTES", 120):
                    for i in range(4):
                        server._append_ui_diag(server._sanitize_ui_diag({
                            "kind": "ui-stuck", "strays": i,
                            "ua_short": "Mozilla/5.0", "askedSid": "sess",
                            "d": {"q": "我的真名"},
                        }))
            p = pathlib.Path(d) / "ui_diag.jsonl"
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("Mozilla", text)
            self.assertNotIn("sess", text)
            self.assertNotIn("我的真名", text)
            self.assertTrue((pathlib.Path(d) / "ui_diag.jsonl.1").is_file(),
                            "超过上限必须轮转到 .1，不能无限增长")


class TestFeedbackPrivacy(unittest.TestCase):
    def test_recent_only_with_owner(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "feedback.jsonl"
            p.write_text(json.dumps({
                "ts": "2026-10-24T00:00:00+08:00", "rating": "down",
                "q": "别人的问题", "comment": "别人的姓名张三",
                "reason": "wrong_clause",
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            with mock.patch.object(server, "_feedback_path", return_value=p):
                public = server._feedback_stats(include_recent=False)
                owner = server._feedback_stats(include_recent=True)
            self.assertEqual(public["recent"], [])
            self.assertEqual(public["down"], 1)
            self.assertEqual(len(owner["recent"]), 1)
            self.assertIn("别人的问题", owner["recent"][0]["q"])

    def test_feedback_rate_limit(self):
        server._FEEDBACK_RATE.reset()
        server._FEEDBACK_RATE.limit = 1
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "feedback.jsonl"
            with mock.patch.object(server, "_feedback_path", return_value=p):
                first, c1 = server.handle_feedback({"rating": "up", "q": "a"})
                second, c2 = server.handle_feedback({"rating": "up", "q": "b"})
            self.assertEqual(c1, 200)
            self.assertEqual(c2, 429)
            self.assertEqual(len(p.read_text(encoding="utf-8").splitlines()), 1)
        server._FEEDBACK_RATE.limit = config.FEEDBACK_PER_MIN


class TestPaidEndpointsGated(unittest.TestCase):
    def _assert_gate(self, fn, body):
        class Boom:
            def slot(self):
                raise C.Overloaded("满载", 7)
        saved = C.REQUEST
        C.REQUEST = Boom()
        try:
            # paid_slot 先 request_budget 再 REQUEST.slot；Boom 抛 Overloaded
            payload, code = fn(body)
        finally:
            C.REQUEST = saved
        self.assertEqual(code, 429)
        self.assertEqual(payload.get("mode"), "overloaded")

    def test_search_gated(self):
        self._assert_gate(server.handle_search, {"q": "跨线防守"})

    def test_branches_gated(self):
        self._assert_gate(server.handle_branches, {"q": "这算犯规吗"})

    def test_practice_gated(self):
        self._assert_gate(server.handle_practice, {"id": "s1", "choice": "yes"})


class TestInputAndTokenEdges(unittest.TestCase):
    def test_top_k_inf_is_clamped_not_500(self):
        self.assertIsNone(server.clamp_top_k("inf"))
        self.assertIsNone(server.clamp_top_k(float("inf")))
        self.assertEqual(server.clamp_top_k("8"), 8)

    def test_non_ascii_owner_token_is_403_not_500(self):
        saved = config.CURATED_TOKEN
        config.CURATED_TOKEN = "tök"
        try:
            self.assertTrue(server._owner_ok(_Headers([("X-Owner-Token", "tök")])))
            # 不能编码的代理字符不能让 compare_digest 抛 TypeError 打成 500
            self.assertFalse(server._owner_ok(_Headers([("X-Owner-Token", "\udcff")])))
        finally:
            config.CURATED_TOKEN = saved

    def test_quiz_public_hides_all_answer_fields(self):
        q = {"id": "s1", "kind": "scenario", "prompt": "p",
             "options": [{"key": "yes", "text": "正确"}],
             "answer": "yes", "why": "因为…", "explain": "这句话是错误的。",
             "evidence": "原文…", "rule_text": "规则原文", "chapter_title": "读秒"}
        pub = server.quiz_public(q)
        self.assertEqual(set(pub), {"id", "kind", "prompt", "options"})

    def test_curated_stats_has_no_server_path(self):
        from app import curated
        st = curated.stats()
        self.assertNotIn("path", st)

    def test_owner_token_only_sent_to_curated(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('if (path !== "/api/curated") return {};', js)
        # askStream 不再带 Owner 头（旧实现会带，等于把写令牌发给只读问答）
        ask = js.split("async function askStream", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("ownerHeaders", ask)


class TestDailyBudget(unittest.TestCase):
    def test_limit_and_reset(self):
        b = C.DailyBudget(2)
        b.charge("llm")
        b.charge("embedding")
        with self.assertRaises(C.Overloaded):
            b.charge("llm")
        snap = b.snapshot()
        self.assertEqual(snap["count"], 2)
        self.assertEqual(snap["by_kind"], {"llm": 1, "embedding": 1})
        b.reset(5)
        b.charge("llm")
        self.assertEqual(b.snapshot()["count"], 1)

    def test_unlimited_zero(self):
        b = C.DailyBudget(0)
        for _ in range(20):
            b.charge("llm")
        self.assertEqual(b.snapshot()["count"], 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
