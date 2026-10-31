"""Offline tests for persistent reauthentication and legacy session recovery."""

import tempfile
import threading
import unittest
from pathlib import Path

from account_outputs import replace_account_session
from reauth_jobs import ReauthManager
from reauthentication import LoginCancelled
from storage.database import Database
from storage.legacy_import import import_legacy_accounts


class ReauthenticationJobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = Database(self.root / "accounts.sqlite3")
        self.account_id, _ = self.database.insert_account({
            "email": "user@example.com", "password": "account-password",
            "sso": "old-session", "source": "legacy_txt",
        })
        self.legacy = self.root / "accounts.txt"
        self.legacy.write_text(
            "user@example.com----account-password----old-session\n"
            "other@example.com----other-pass----other-session\n", encoding="utf-8",
        )
        self.database.add_artifact(self.account_id, "legacy_txt", str(self.legacy), "txt")

    def _run(self, login=None, postprocess=None, account_ids=None):
        manager = ReauthManager(
            self.database,
            login or (lambda email, password, **_kwargs: "new-session"),
            postprocess or (lambda *_args, **_kwargs: (
                "succeeded", "", "succeeded", "", "not_requested", "",
            )),
            self.root,
        )
        job_id = manager.start(account_ids or [self.account_id])
        manager._thread.join(timeout=5)
        self.assertFalse(manager._thread.is_alive())
        return self.database.get_job(job_id)

    def test_success_refreshes_database_and_legacy_txt_without_duplicates(self):
        result = self._run()
        account = self.database.get_account(self.account_id)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["items"][0]["status"], "succeeded")
        self.assertEqual(account["sso"], "new-session")
        self.assertEqual(account["authorization_status"], "authorized")
        self.assertIsNotNone(account["last_authenticated_at"])
        self.assertEqual(account["cpa_status"], "succeeded")
        self.assertEqual(account["grok2api_local_status"], "succeeded")
        self.assertEqual(self.legacy.read_text(encoding="utf-8"),
            "user@example.com----account-password----new-session\n"
            "other@example.com----other-pass----other-session\n",
        )

        # Importing the original, now obsolete token cannot resurrect a second account.
        old_file = self.root / "old-snapshot.txt"
        old_file.write_text(
            "user@example.com----account-password----old-session\n", encoding="utf-8"
        )
        import_legacy_accounts(self.database, old_file)
        self.assertEqual(self.database.account_count(), 1)

    def test_failed_login_preserves_old_authorization_and_file(self):
        original = self.legacy.read_text(encoding="utf-8")

        def rejected(*_args, **_kwargs):
            raise RuntimeError("login rejected")

        result = self._run(login=rejected)

        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["items"][0]["status"], "failed")
        self.assertEqual(self.database.get_account(self.account_id)["sso"], "old-session")
        self.assertEqual(self.legacy.read_text(encoding="utf-8"), original)

    def test_downstream_failure_is_a_warning_not_failed_login(self):
        def fail_export(*_args, **_kwargs):
            raise RuntimeError("CPA backend unavailable")

        result = self._run(postprocess=fail_export)

        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["items"][0]["status"], "succeeded")
        self.assertIn("授权后处理失败", result["items"][0]["error_message"])
        account = self.database.get_account(self.account_id)
        self.assertEqual(account["sso"], "new-session")
        self.assertEqual(account["cpa_status"], "unknown")
        self.assertEqual(account["grok2api_local_status"], "unknown")

    def test_missing_password_is_recorded_without_attempting_login(self):
        second, _ = self.database.insert_account({
            "email": "missing@example.com", "sso": "old", "password": "",
        })
        calls = []
        result = self._run(
            account_ids=[second, self.account_id],
            login=lambda email, password, **_kwargs: (
                calls.append(email) or "new-session"
            ),
        )

        self.assertEqual(result["requested_count"], 2)
        self.assertEqual(result["processed_count"], 2)
        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(calls, ["user@example.com"])

    def test_stop_during_login_preserves_existing_session(self):
        entered = threading.Event()

        def login(_email, _password, cancelled, **_kwargs):
            entered.set()
            while not cancelled():
                threading.Event().wait(0.01)
            raise LoginCancelled("stopped")

        manager = ReauthManager(
            self.database, login,
            lambda *_args, **_kwargs: self.fail("postprocess should not run"),
            self.root,
        )
        job_id = manager.start([self.account_id])
        self.assertTrue(entered.wait(timeout=3))
        self.assertTrue(manager.stop())
        manager._thread.join(timeout=3)
        result = self.database.get_job(job_id)

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["items"][0]["status"], "skipped")
        self.assertEqual(self.database.get_account(self.account_id)["sso"], "old-session")

    def test_duplicate_account_selection_is_deduplicated(self):
        result = self._run(account_ids=[self.account_id, self.account_id])
        self.assertEqual(result["requested_count"], 1)
        self.assertEqual(len(result["items"]), 1)

    def test_legacy_replacement_updates_only_target_identity(self):
        replace_account_session(
            self.legacy, "user@example.com", "new-password",
            "old-session", "new-session",
        )
        replace_account_session(
            self.legacy, "user@example.com", "new-password",
            "old-session", "new-session",
        )
        lines = self.legacy.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, [
            "user@example.com----new-password----new-session",
            "other@example.com----other-pass----other-session",
        ])

    def test_two_accounts_with_same_email_are_tracked_by_account_id(self):
        second, _ = self.database.insert_account({
            "email": "user@example.com", "password": "pw2", "sso": "different-token",
        })
        result = self._run(
            account_ids=[self.account_id, second],
            login=lambda _email, password, **_kwargs: "new-session-" + password,
        )
        self.assertEqual(result["requested_count"], 2)
        self.assertEqual(result["processed_count"], 2)
        self.assertEqual(result["success_count"], 2)
        self.assertEqual({item["account_id"] for item in result["items"]}, {
            self.account_id, second,
        })


if __name__ == "__main__":
    unittest.main()
