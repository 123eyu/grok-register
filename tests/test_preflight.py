import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import preflight
from app_config import DEFAULT_CONFIG


class UnifiedHealthCheckTests(unittest.TestCase):
    def config(self, **updates):
        return dict(DEFAULT_CONFIG, **updates)

    def by_key(self, result):
        return {item["key"]: item for item in result["checks"]}

    def test_static_checks_do_not_run_network_or_browser_probes(self):
        with patch.object(preflight, "_check_browser_connection", side_effect=AssertionError), \
             patch.object(preflight, "_check_proxy_connection", side_effect=AssertionError), \
             patch.object(preflight, "_check_mailbox_connection", side_effect=AssertionError), \
             patch.object(preflight, "_check_cpa_connection", side_effect=AssertionError), \
             patch.object(preflight, "_check_grok2api_connection", side_effect=AssertionError):
            result = preflight.run_health_checks(self.config())

        checks = self.by_key(result)
        self.assertTrue(result["ready"])
        self.assertEqual(result["result"], "Ready")
        self.assertFalse(result["connectivity"])
        self.assertTrue(all(not item["connection_tested"] for item in checks.values()))
        self.assertEqual(
            [item["name"] for item in result["checks"]],
            ["Configuration", "Browser Runtime", "Proxy", "Mailbox", "CPA", "Grok2API"],
        )

    def test_missing_required_mail_configuration_is_an_error(self):
        result = preflight.run_health_checks(self.config(
            email_provider="cloudmail",
            cloudmail_api_base="",
            cloudmail_public_token="",
            cloudmail_domains="",
            cpa_export_enabled=False,
        ))
        checks = self.by_key(result)

        self.assertFalse(result["ready"])
        self.assertEqual(checks["configuration"]["status"], "OK")
        self.assertEqual(checks["mailbox"]["status"], "ERROR")
        self.assertTrue(checks["mailbox"]["required"])

    def test_optional_cpa_failure_is_a_warning_not_a_blocker(self):
        with patch("cpa_export._load_mint_and_export", side_effect=ImportError("missing package")):
            result = preflight.run_health_checks(self.config(cpa_export_enabled=True))
        cpa = self.by_key(result)["cpa"]

        self.assertTrue(result["ready"])
        self.assertEqual(result["result"], "Ready")
        self.assertEqual(cpa["status"], "WARN")
        self.assertFalse(cpa["required"])

    def test_invalid_optional_local_token_file_is_a_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "token.json"
            token_file.write_text("{broken", encoding="utf-8")
            result = preflight.run_health_checks(self.config(
                cpa_export_enabled=False,
                grok2api_auto_add_local=True,
                grok2api_local_token_file=str(token_file),
            ))
        check = self.by_key(result)["grok2api"]

        self.assertTrue(result["ready"])
        self.assertEqual(check["status"], "WARN")
        self.assertFalse(check["required"])

    def test_required_proxy_probe_failure_blocks_readiness(self):
        config = self.config(
            cpa_export_enabled=False,
            proxy_mode="single",
            proxy="http://127.0.0.1:7890",
        )
        manager = Mock()
        manager.reload_sources.return_value = {}
        manager.probe_all.return_value = [{"id": "node-1", "status": "unhealthy"}]
        with patch("proxy_pool.get_manager", return_value=manager):
            result = preflight.run_health_checks(config, connectivity=True)
        check = self.by_key(result)["proxy"]

        self.assertFalse(result["ready"])
        self.assertEqual(check["status"], "ERROR")
        self.assertTrue(check["required"])
        manager.reload_sources.assert_called_once_with(force=True)
        manager.probe_all.assert_called_once_with(force=True)

    def test_optional_remote_services_are_not_probed_when_disabled(self):
        with patch.object(preflight, "_check_cpa_connection", side_effect=AssertionError), \
             patch.object(preflight, "_check_grok2api_connection", side_effect=AssertionError), \
             patch.object(preflight, "_check_browser_connection", return_value="browser ok"), \
             patch.object(preflight, "_check_proxy_connection", return_value="network ok"), \
             patch.object(preflight, "_check_mailbox_connection", return_value="mail ok"):
            result = preflight.run_health_checks(
                self.config(cpa_export_enabled=False), connectivity=True
            )
        checks = self.by_key(result)

        self.assertTrue(result["ready"])
        self.assertFalse(checks["cpa"]["connection_tested"])
        self.assertFalse(checks["grok2api"]["connection_tested"])
        self.assertTrue(checks["browser"]["connection_tested"])
        self.assertTrue(checks["proxy"]["connection_tested"])
        self.assertTrue(checks["mailbox"]["connection_tested"])

    def test_mailbox_connection_reuses_existing_provider_probe(self):
        config = self.config(cpa_export_enabled=False, email_provider="duckmail")
        with patch("mail_service.get_domains", return_value=[{"domain": "example.test"}]) as probe:
            message = preflight._check_mailbox_connection(config)
        self.assertEqual(message, "服务连接成功，可用域名: 1")
        probe.assert_called_once_with()

    def test_mailbox_probe_restores_shared_runtime_configuration(self):
        import mail_service

        original = mail_service.config
        candidate = self.config(email_provider="duckmail")
        with patch("mail_service.get_domains", return_value=[{"domain": "example.test"}]):
            preflight._check_mailbox_connection(candidate)
        self.assertIs(mail_service.config, original)

    def test_outlook_probe_reports_healthy_count_without_exposing_credentials(self):
        config = self.config(
            email_provider="outlook",
            outlook_accounts_file="outlook-accounts.txt",
        )
        summary = {"count": 2, "healthy": 1, "unhealthy": 1}
        with patch("outlook_mailbox_pool.probe_outlook_mailbox_pool", return_value=summary) as probe:
            message = preflight._check_mailbox_connection(config)
        self.assertEqual(message, "Outlook 可用邮箱: 1/2")
        probe.assert_called_once_with("outlook-accounts.txt")

    def test_safe_error_redacts_configured_secrets(self):
        config = self.config(cloudmail_public_token="public-secret")
        message = preflight._safe_error(
            RuntimeError("request failed with public-secret"), config
        )
        self.assertNotIn("public-secret", message)
        self.assertIn("[REDACTED]", message)

    def test_summary_counts_only_required_errors_as_blocking(self):
        result = preflight.run_health_checks(self.config(cpa_export_enabled=False))
        self.assertTrue(result["ok"])
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["warning_count"], 0)
        self.assertIn("checked_at", result)


if __name__ == "__main__":
    unittest.main()
