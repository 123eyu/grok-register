import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "web dependencies not installed")
class DependencyHealthCheckWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web import server
        cls.server = server
        cls.client = TestClient(server.app)

    def setUp(self):
        with self.server._job_lock:
            self.server._job_state["running"] = False
            self.server._maintenance_state = None

    def test_static_health_check_uses_saved_config_and_returns_no_store_summary(self):
        summary = {
            "ok": True,
            "ready": True,
            "result": "Ready",
            "connectivity": False,
            "error_count": 0,
            "warning_count": 0,
            "checks": [],
        }
        with patch.object(self.server.engine, "load_config"), patch(
            "preflight.run_health_checks", return_value=summary
        ) as check:
            response = self.client.post("/api/health-check", json={"connectivity": False})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertEqual(response.json()["result"], "Ready")
        self.assertEqual(check.call_args.args[1], False)

    def test_connectivity_check_is_explicitly_forwarded(self):
        summary = {
            "ok": True, "ready": True, "result": "Ready",
            "connectivity": True, "error_count": 0, "warning_count": 0, "checks": [],
        }
        with patch.object(self.server.engine, "load_config"), patch(
            "preflight.run_health_checks", return_value=summary
        ) as check:
            response = self.client.post("/api/health-check", json={"connectivity": True})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["connectivity"])
        self.assertEqual(check.call_args.args[1], True)

    def test_foreign_origin_is_rejected(self):
        response = self.client.post(
            "/api/health-check",
            json={"connectivity": False},
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_health_check_is_rejected_while_registration_runs(self):
        with self.server._job_lock:
            self.server._job_state["running"] = True
        response = self.client.post("/api/health-check", json={"connectivity": True})
        self.assertEqual(response.status_code, 409)

    def test_connectivity_flag_must_be_boolean(self):
        response = self.client.post("/api/health-check", json={"connectivity": "yes"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
