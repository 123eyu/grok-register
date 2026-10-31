"""Tests for SQLite account persistence and independent downstream states."""

from types import SimpleNamespace
import tempfile
import unittest
from pathlib import Path

from storage.account_service import persist_account, persist_account_status
from storage.database import Database


class AccountServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.database = Database(root / "data" / "accounts.sqlite3")
        self.output_path = root / "accounts.txt"
        self.run_id = self.database.create_registration_run(1)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sqlite_is_primary_and_legacy_export_is_idempotent(self):
        account = SimpleNamespace(
            email="user@example.com",
            password="account-password",
            sso="sso-secret",
            profile={"given_name": "Test"},
        )
        first = persist_account(self.database, self.output_path, account, "duckmail", self.run_id)
        second = persist_account(self.database, self.output_path, account, "duckmail", self.run_id)

        self.assertEqual(first["account_id"], second["account_id"])
        self.assertTrue(first["inserted"])
        self.assertFalse(second["inserted"])
        self.assertEqual(
            self.output_path.read_text(encoding="utf-8"),
            "user@example.com----account-password----sso-secret\n",
        )
        stored = self.database.get_account(first["account_id"])
        self.assertEqual(stored["email_provider"], "duckmail")
        self.assertEqual(stored["registration_run_id"], self.run_id)
        self.assertEqual(len(stored["artifacts"]), 1)
        self.assertEqual(stored["artifacts"][0]["artifact_type"], "legacy_txt")
        self.assertEqual(stored["artifacts"][0]["path"], str(self.output_path.resolve()))

    def test_existing_legacy_export_is_updated_when_password_changes(self):
        account = SimpleNamespace(
            email="user@example.com",
            password="old-password",
            sso="same-sso",
            profile={},
        )
        first = persist_account(self.database, self.output_path, account, "duckmail", self.run_id)
        updated_account = SimpleNamespace(
            email="user@example.com",
            password="new-password",
            sso="same-sso",
            profile={},
        )
        second = persist_account(
            self.database, self.output_path, updated_account, "duckmail", self.run_id
        )

        self.assertEqual(first["account_id"], second["account_id"])
        self.assertFalse(second["inserted"])
        self.assertEqual(
            self.output_path.read_text(encoding="utf-8"),
            "user@example.com----new-password----same-sso\n",
        )
        self.assertEqual(
            self.database.get_account(first["account_id"])["password"], "new-password"
        )

    def test_integration_statuses_are_saved_without_credentials_in_errors(self):
        account = SimpleNamespace(
            email="user@example.com",
            password="account-password",
            sso="sso-secret",
            profile={},
        )
        persisted = persist_account(self.database, self.output_path, account, "duckmail", self.run_id)
        result = persist_account_status(
            self.database,
            persisted["account_id"],
            {
                "local": {"enabled": True, "ok": True},
                "remote": {"enabled": True, "ok": False, "error": "failed with sso-secret"},
            },
            {"ok": False, "skipped": False, "error": "CPA failed with account-password"},
            password=account.password,
            sso=account.sso,
        )

        self.assertTrue(result)
        stored = self.database.get_account(persisted["account_id"])
        self.assertEqual(stored["cpa_status"], "failed")
        self.assertEqual(stored["grok2api_local_status"], "succeeded")
        self.assertEqual(stored["grok2api_remote_status"], "failed")
        self.assertNotIn("account-password", stored["cpa_error"])
        self.assertNotIn("sso-secret", stored["grok2api_remote_error"])
        self.assertIn("[REDACTED]", stored["cpa_error"])
        self.assertIn("[REDACTED]", stored["grok2api_remote_error"])


if __name__ == "__main__":
    unittest.main()
