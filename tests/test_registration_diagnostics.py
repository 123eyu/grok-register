"""Offline tests for registration failure screenshot capture."""

import os
import tempfile
import unittest
from pathlib import Path

from registration_diagnostics import capture_failure_screenshot


class RegistrationDiagnosticsTests(unittest.TestCase):
    def test_fake_page_saves_private_screenshot_with_attempt_context(self):
        class FakePage:
            def __init__(self):
                self.full_page = None

            def get_screenshot(self, path, full_page=False):
                self.full_page = full_page
                Path(path).write_bytes(b"fake-png")

        with tempfile.TemporaryDirectory() as directory:
            page = FakePage()
            path = capture_failure_screenshot(
                page,
                42,
                {
                    "slot_index": 3,
                    "attempt_number": 2,
                    "worker_key": "grok-register_1",
                },
                screenshot_dir=directory,
            )

            saved = Path(path)
            self.assertTrue(saved.is_file())
            self.assertEqual(saved.read_bytes(), b"fake-png")
            self.assertIn("run42_slot3_attempt2_grok-register_1", saved.name)
            self.assertTrue(page.full_page)
            if os.name == "posix":
                self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
                self.assertEqual(saved.parent.stat().st_mode & 0o777, 0o700)

    def test_page_screenshot_error_returns_empty_path_and_logs(self):
        class BrokenPage:
            def get_screenshot(self, path, full_page=False):
                raise TypeError("screenshot implementation failed")

        with tempfile.TemporaryDirectory() as directory:
            logs = []
            path = capture_failure_screenshot(
                BrokenPage(), 1, {"slot_index": 1, "attempt_number": 1},
                screenshot_dir=directory, log_callback=logs.append,
            )
            self.assertEqual(path, "")
            self.assertEqual(len(logs), 1)
            self.assertIn("TypeError", logs[0])

    def test_missing_or_failed_page_returns_empty_path(self):
        class BrokenPage:
            def get_screenshot(self, path, full_page=False):
                Path(path).write_bytes(b"partial")
                raise RuntimeError("browser disconnected")

        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                capture_failure_screenshot(None, 1, {}, screenshot_dir=directory), ""
            )
            self.assertEqual(
                capture_failure_screenshot(BrokenPage(), 1, {}, screenshot_dir=directory), ""
            )
            self.assertEqual(list(Path(directory).glob("*.png")), [])


if __name__ == "__main__":
    unittest.main()
