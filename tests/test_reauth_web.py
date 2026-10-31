"""Web API tests for reauthentication, with all browser/network operations mocked."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "web dependencies not installed")
class ReauthenticationWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web import server
        cls.server = server
        cls.client = TestClient(server.app)

    def setUp(self):
        from storage.database import Database

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = Database(self.root / "accounts.sqlite3")
        self.account_id, _ = self.database.insert_account({
            "email": "web@example.com", "password": "password",
            "sso": "old-session",
        })
        with self.server._job_lock:
            self.server._job_state["running"] = False
            self.server._maintenance_state = None
        self.server._reauth_manager = None

    def _start(self):
        with patch.object(self.server, "_get_database", return_value=self.database), \
             patch.object(self.server, "ROOT", self.root), \
             patch.object(self.server.engine, "load_config"), \
             patch("reauth_jobs.browser_login", return_value="new-session"), \
             patch("reauth_jobs.refresh_integrations", return_value=(
                 "not_requested", "", "not_requested", "", "not_requested", "",
             )):
            response = self.client.post(
                "/api/reauth/start", json={"ids": [self.account_id]}
            )
            self.assertEqual(response.status_code, 200)
            self.server._reauth_manager._thread.join(timeout=3)
            self.assertFalse(self.server._reauth_manager._thread.is_alive())
        return response.json()["job_id"]

    def test_start_status_history_and_account_session(self):
        job_id = self._start()

        with patch.object(self.server, "_get_database", return_value=self.database):
            status = self.client.get("/api/reauth/status")
            history = self.client.get("/api/reauth/history")
            detail = self.client.get("/api/reauth/jobs/%s" % job_id)

        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json()["running"])
        self.assertEqual(status.json()["job"]["status"], "completed")
        self.assertEqual(status.json()["job"]["success_count"], 1)
        self.assertEqual(history.json()["items"][0]["id"], job_id)
        self.assertEqual(history.headers["cache-control"], "no-store")
        self.assertEqual(detail.json()["job"]["items"][0]["status"], "succeeded")
        self.assertEqual(self.database.get_account(self.account_id)["sso"], "new-session")
        self.assertNotIn("password", str(detail.json()))
        with self.server._job_lock:
            self.assertIsNone(self.server._maintenance_state)

    def test_invalid_ids_and_unrelated_origins_are_rejected(self):
        for ids in ([], [True], [1.5], [-1]):
            result = self.client.post("/api/reauth/start", json={"ids": ids})
            self.assertEqual(result.status_code, 400)
        foreign = self.client.post(
            "/api/reauth/start", json={"ids": [self.account_id]},
            headers={"origin": "https://attacker.example"},
        )
        self.assertEqual(foreign.status_code, 403)

    def test_reauthentication_cannot_overlap_active_registration(self):
        with self.server._job_lock:
            self.server._job_state["running"] = True
        result = self.client.post(
            "/api/reauth/start", json={"ids": [self.account_id]}
        )
        self.assertEqual(result.status_code, 409)
        with self.server._job_lock:
            self.server._job_state["running"] = False

    def test_account_deletion_is_blocked_during_reauthentication(self):
        with self.server._job_lock:
            self.server._maintenance_state = "account_reauthentication"
        try:
            single = self.client.delete("/api/accounts/%s" % self.account_id)
            bulk = self.client.request(
                "DELETE", "/api/accounts", json={"ids": [self.account_id]}
            )
        finally:
            with self.server._job_lock:
                self.server._maintenance_state = None
        self.assertEqual(single.status_code, 409)
        self.assertEqual(bulk.status_code, 409)
        self.assertIsNotNone(self.database.get_account(self.account_id))

    def test_stop_endpoint_forwards_to_active_manager(self):
        from unittest.mock import Mock

        fake_manager = Mock()
        fake_manager.stop.return_value = True
        self.server._reauth_manager = fake_manager
        result = self.client.post("/api/reauth/stop")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["stopped"])
        fake_manager.stop.assert_called_once_with()

    def test_missing_account_is_rejected_and_maintenance_is_released(self):
        with patch.object(self.server, "_get_database", return_value=self.database), \
             patch.object(self.server.engine, "load_config"):
            response = self.client.post("/api/reauth/start", json={"ids": [999]})
        self.assertEqual(response.status_code, 400)
        with self.server._job_lock:
            self.assertIsNone(self.server._maintenance_state)


if __name__ == "__main__":
    unittest.main()
