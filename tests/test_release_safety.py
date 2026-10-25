"""Regression checks for public deployment controls."""
import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import capacity, config, server
from scripts import build_index, setup


class PublicAccessTests(unittest.TestCase):
    def test_non_loopback_bind_requires_long_password_before_boot(self):
        with patch.object(server, "HOST", "0.0.0.0"), \
             patch.object(config, "PUBLIC_ACCESS_PASSWORD", "too-short"), \
             patch.object(server, "boot") as boot:
            self.assertEqual(server.main(), 2)
            boot.assert_not_called()

    def test_password_check(self):
        handler = object.__new__(server.Handler)
        with patch.object(config, "PUBLIC_ACCESS_PASSWORD", "a-long-test-password"):
            handler.headers = {}
            self.assertFalse(handler._access_ok())
            token = base64.b64encode(b"shultimate:a-long-test-password").decode()
            handler.headers = {"Authorization": "Basic " + token}
            self.assertTrue(handler._access_ok())
            handler.headers = {"Authorization": "Basic " + base64.b64encode(b"shultimate:wrong").decode()}
            self.assertFalse(handler._access_ok())

    def test_curated_read_requires_owner_token(self):
        handler = object.__new__(server.Handler)
        handler.path = "/api/curated"
        handler._require_access = lambda: True
        sent = []
        handler._send_json = lambda obj, code=200: sent.append((code, obj))
        with patch.object(config, "CURATED_TOKEN", "owner-secret"), \
             patch.object(server.curated_mod, "all_items", return_value=[{"question": "private"}]):
            handler.headers = {}
            handler.do_GET()
            self.assertEqual(sent[-1][0], 403)
            self.assertNotIn("private", str(sent[-1][1]))
            handler.headers = {"X-Owner-Token": "owner-secret"}
            handler.do_GET()
            self.assertEqual(sent[-1][0], 200)
            self.assertEqual(sent[-1][1]["items"][0]["question"], "private")


class BudgetTests(unittest.TestCase):
    def test_paid_limit_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.sqlite3"
            first = capacity.DailyBudget(2, path)
            first.charge("embedding")
            second = capacity.DailyBudget(2, path)
            second.charge("llm")
            self.assertEqual(second.snapshot()["count"], 2)
            with self.assertRaises(capacity.Overloaded):
                capacity.DailyBudget(2, path).charge("llm")


class InstallerSettingsTests(unittest.TestCase):
    def test_port_comes_from_local_env(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            env.write_text("PORT=8017\n", encoding="utf-8")
            with patch.object(setup, "ENV_FILE", env), patch.dict("os.environ", {}, clear=True):
                self.assertEqual(setup._setting("PORT", "8000"), "8017")


class IndexFreshnessTests(unittest.TestCase):
    def test_check_detects_corpus_change_without_calling_model(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            item = {"id": "1.1", "search_text": "first"}
            (data / "rules.jsonl").write_text(
                json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            fingerprint = hashlib.sha256(
                json.dumps([item], ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            (data / "embeddings.bin").write_bytes(b"\x00" * 8)
            (data / "index.json").write_text(
                json.dumps({
                    "corpus_sha256": fingerprint,
                    "model": "test-model",
                    "dims": 2,
                    "query_prefix": "query: ",
                    "items": [item],
                }),
                encoding="utf-8",
            )
            with patch.object(config, "DATA_DIR", data), \
                 patch.object(config, "EMBED_MODEL", "test-model"), \
                 patch.object(config, "EMBED_DIMS", 2), \
                 patch.object(config, "QUERY_PREFIX", "query: "), \
                 patch.object(build_index.sys, "argv", ["build_index.py", "--check"]):
                self.assertEqual(build_index.main(), 0)
                item["search_text"] = "changed"
                (data / "rules.jsonl").write_text(
                    json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8"
                )
                self.assertEqual(build_index.main(), 3)


if __name__ == "__main__":
    unittest.main()
