"""Offline checks for the guided installer; no model API is called."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import setup


class SetupTests(unittest.TestCase):
    def test_offline_check_never_reads_key_or_starts_service(self):
        with patch.object(setup.sys, "argv", ["setup.py", "--offline-check"]), \
             patch.object(setup, "_ensure_pdf_reader", return_value=True), \
             patch.object(setup, "_run_step", return_value=True) as run_step, \
             patch.object(setup, "_run_offline_tests", return_value=True) as run_tests, \
             patch.object(setup, "_ensure_key") as ensure_key, \
             patch.object(setup.subprocess, "Popen") as popen:
            self.assertEqual(setup.main(), 0)
            run_step.assert_called_once_with("scripts/build_corpus.py")
            run_tests.assert_called_once_with()
            ensure_key.assert_not_called()
            popen.assert_not_called()

    def test_key_stays_in_local_env_and_existing_options_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            example = root / ".env.example"
            env = root / ".env"
            example.write_text("ZHIPU_API_KEY=\nPORT=8000\n", encoding="utf-8")
            setup._save_key(env, example, "test-key-one")
            self.assertEqual(env.read_text(encoding="utf-8"),
                             "ZHIPU_API_KEY=test-key-one\nPORT=8000\n")
            setup._save_key(env, example, "test-key-two")
            self.assertEqual(env.read_text(encoding="utf-8").count("ZHIPU_API_KEY="), 1)
            self.assertIn("PORT=8000", env.read_text(encoding="utf-8"))
            if os.name != "nt":
                self.assertEqual(env.stat().st_mode & 0o077, 0)

    def test_missing_key_does_not_start_setup_in_noninteractive_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            example = Path(directory) / ".env.example"
            example.write_text("ZHIPU_API_KEY=\n", encoding="utf-8")
            with patch.dict(os.environ, {"ZHIPU_API_KEY": ""}, clear=False), \
                 patch.object(setup, "ENV_FILE", env), \
                 patch.object(setup, "ENV_EXAMPLE", example), \
                 patch.object(setup.sys.stdin, "isatty", return_value=False):
                self.assertFalse(setup._ensure_key())
                self.assertEqual(env.read_text(encoding="utf-8"), "ZHIPU_API_KEY=\n")

    def test_index_charges_need_interactive_confirmation(self):
        with patch.object(setup.sys.stdin, "isatty", return_value=False):
            self.assertFalse(setup._confirm_index_cost())
        with patch.object(setup.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", return_value="n"):
            self.assertFalse(setup._confirm_index_cost())
        with patch.object(setup.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", return_value="y"):
            self.assertTrue(setup._confirm_index_cost())
        with patch.object(setup.sys.stdin, "isatty", return_value=False):
            self.assertTrue(setup._confirm_index_cost(approved=True))


if __name__ == "__main__":
    unittest.main()
