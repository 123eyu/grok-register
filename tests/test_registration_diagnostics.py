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

    def test_fake_page_without_full_page_argument_uses_compatible_signature(self):
        class FakePage:
            def get_screenshot(self, path):
                Path(path).write_bytes(b"fake-png")

        with tempfile.TemporaryDirectory() as directory:
            path = capture_failure_screenshot(
                FakePage(), 1, {"slot_index": 1, "attempt_number": 1},
                screenshot_dir=directory,
            )
            self.assertTrue(Path(path).is_file())

    def test_missing_or_failed_page_returns_empty_path(self):
        class BrokenPage:
            def get_screenshot(self, **_kwargs):
                raise RuntimeError("browser disconnected")

        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                capture_failure_screenshot(None, 1, {}, screenshot_dir=directory), ""
            )
            self.assertEqual(
                capture_failure_screenshot(BrokenPage(), 1, {}, screenshot_dir=directory), ""
            )


if __name__ == "__main__":
    unittest.main()
