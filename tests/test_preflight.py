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

    def test_unexpected_static_check_errors_are_reported_without_aborting(self):
        with patch.object(preflight, "_check_browser_runtime", side_effect=OSError("runtime inspect failed")):
            result = preflight.run_health_checks(self.config(cpa_export_enabled=False))

        browser = self.by_key(result)["browser"]
        self.assertFalse(result["ready"])
        self.assertEqual(browser["status"], "ERROR")
        self.assertIn("runtime inspect failed", browser["message"])

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
        with patch("cpa_export.CpaExportSettings.from_config", side_effect=ImportError("missing package")):
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

    def test_browser_probe_always_stops_browser_after_start_failure(self):
        with patch("registration_browser.start_browser", side_effect=RuntimeError("launch failed")), \
             patch("registration_browser.stop_browser") as stop:
            with self.assertRaisesRegex(RuntimeError, "launch failed"):
                preflight._check_browser_connection()
        stop.assert_called_once_with()

    def test_required_mailbox_connectivity_failure_blocks_readiness(self):
        with patch.object(preflight, "_check_browser_connection", return_value="browser ok"), \
             patch.object(preflight, "_check_proxy_connection", return_value="network ok"), \
             patch.object(preflight, "_check_mailbox_connection", side_effect=RuntimeError("mail offline")):
            result = preflight.run_health_checks(
                self.config(cpa_export_enabled=False), connectivity=True
            )

        self.assertFalse(result["ready"])
        mailbox = self.by_key(result)["mailbox"]
        self.assertEqual(mailbox["status"], "ERROR")
        self.assertTrue(mailbox["connection_tested"])

    def test_optional_cpa_connectivity_failure_remains_a_warning(self):
        with patch.object(preflight, "_check_cpa_config", return_value=preflight._result(
            "cpa", "CPA", "OK", False, "CPA config ok"
        )), patch.object(preflight, "_check_browser_connection", return_value="browser ok"), \
             patch.object(preflight, "_check_proxy_connection", return_value="network ok"), \
             patch.object(preflight, "_check_mailbox_connection", return_value="mail ok"), \
             patch.object(preflight, "_check_cpa_connection", side_effect=RuntimeError("CPA offline")):
            result = preflight.run_health_checks(
                self.config(cpa_export_enabled=True), connectivity=True
            )

        self.assertTrue(result["ready"])
        cpa = self.by_key(result)["cpa"]
        self.assertEqual(cpa["status"], "WARN")
        self.assertFalse(cpa["required"])
        self.assertTrue(cpa["connection_tested"])

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

    def test_proxy_health_logs_redact_configured_proxy_credentials(self):
        config = self.config(
            proxy_mode="single",
            proxy="http://user:proxy-secret@127.0.0.1:7890",
        )
        manager = Mock()
        manager.probe_all.return_value = [{"id": "single", "status": "healthy"}]
        logs = []

        def get_manager(config, log):
            log("proxy source failed: " + config["proxy"])
            return manager

        with patch("proxy_pool.get_manager", side_effect=get_manager):
            preflight._check_proxy_connection(config, log_callback=logs.append)

        self.assertEqual(len(logs), 1)
        self.assertNotIn("proxy-secret", logs[0])
        self.assertIn("[REDACTED]", logs[0])

    def test_safe_error_redacts_configured_secrets(self):
        config = self.config(cloudmail_public_token="public-secret")
        message = preflight._safe_error(
            RuntimeError("request failed with public-secret"), config
        )
        self.assertNotIn("public-secret", message)
        self.assertIn("[REDACTED]", message)

    def test_cpa_static_check_does_not_execute_external_package_code(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "cpa_xai"
            package.mkdir()
            (package / "__init__.py").write_text(
                "raise RuntimeError('must not execute during static check')\\n",
                encoding="utf-8",
            )
            result = preflight.run_health_checks(self.config(
                cpa_export_enabled=True,
                api_reverse_tools=directory,
            ))

        cpa = self.by_key(result)["cpa"]
        self.assertTrue(result["ready"])
        self.assertEqual(cpa["status"], "OK")

    def test_invalid_optional_cpa_endpoint_is_a_warning(self):
        result = preflight.run_health_checks(self.config(
            cpa_export_enabled=True,
            cpa_base_url="not-a-url",
        ))
        checks = self.by_key(result)

        self.assertTrue(result["ready"])
        self.assertEqual(checks["configuration"]["status"], "OK")
        self.assertEqual(checks["cpa"]["status"], "WARN")
        self.assertFalse(checks["cpa"]["required"])

    def test_invalid_optional_cpa_timeout_is_a_warning(self):
        result = preflight.run_health_checks(self.config(
            cpa_export_enabled=True,
            cpa_mint_timeout_sec=5,
        ))
        checks = self.by_key(result)

        self.assertTrue(result["ready"])
        self.assertEqual(checks["configuration"]["status"], "OK")
        self.assertEqual(checks["cpa"]["status"], "WARN")

    def test_invalid_optional_grok2api_remote_endpoint_is_a_warning(self):
        result = preflight.run_health_checks(self.config(
            cpa_export_enabled=False,
            grok2api_auto_add_remote=True,
            grok2api_remote_base="not-a-url",
            grok2api_remote_app_key="app-key",
        ))
        check = self.by_key(result)["grok2api"]

        self.assertTrue(result["ready"])
        self.assertEqual(check["status"], "WARN")
        self.assertFalse(check["required"])

    def test_proxy_pool_static_check_rejects_unparseable_source_file(self):
        with tempfile.TemporaryDirectory() as directory:
            pool_file = Path(directory) / "proxies.txt"
            pool_file.write_text("not a proxy node\\n", encoding="utf-8")
            result = preflight.run_health_checks(self.config(
                cpa_export_enabled=False,
                proxy_mode="pool",
                proxy_pool_file=str(pool_file),
            ))

        check = self.by_key(result)["proxy"]
        self.assertFalse(result["ready"])
        self.assertEqual(check["status"], "ERROR")
        self.assertIn("代理源中没有可用节点", check["message"])

    def test_proxy_pool_uses_any_valid_source_and_warns_for_invalid_source(self):
        with tempfile.TemporaryDirectory() as directory:
            pool_file = Path(directory) / "proxies.txt"
            pool_file.write_text("http://127.0.0.1:7890\n", encoding="utf-8")
            result = preflight.run_health_checks(self.config(
                cpa_export_enabled=False,
                proxy_mode="pool",
                proxy_pool_file=str(pool_file),
                proxy_pool_subscription_url="not-a-url",
            ))
        check = self.by_key(result)["proxy"]

        self.assertTrue(result["ready"])
        self.assertEqual(check["status"], "WARN")
        self.assertTrue(check["required"])
        self.assertIn("至少一个代理池来源可用", check["message"])

    def test_safe_error_redacts_proxy_and_endpoint_credentials(self):
        config = self.config(
            proxy="http://user:proxy-secret@127.0.0.1:7890",
            grok2api_remote_base="https://remote.example/api?token=remote-secret",
        )
        message = preflight._safe_error(
            RuntimeError(
                "proxy http://user:proxy-secret@127.0.0.1:7890 "
                "remote https://remote.example/api?token=remote-secret"
            ),
            config,
        )
        self.assertNotIn("proxy-secret", message)
        self.assertNotIn("remote-secret", message)

    def test_unknown_proxy_mode_and_mailbox_provider_are_errors(self):
        proxy = preflight.run_health_checks(self.config(
            proxy_mode="unknown",
            cpa_export_enabled=False,
        ))
        self.assertEqual(self.by_key(proxy)["proxy"]["status"], "ERROR")

        mailbox = preflight.run_health_checks(self.config(
            email_provider="unknown",
            cpa_export_enabled=False,
        ))
        self.assertEqual(self.by_key(mailbox)["mailbox"]["status"], "ERROR")

    def test_cloudflare_auth_mode_requires_its_api_key(self):
        result = preflight.run_health_checks(self.config(
            email_provider="cloudflare",
            cloudflare_api_base="https://mail.example",
            cloudflare_auth_mode="bearer",
            cloudflare_api_key="",
            cpa_export_enabled=False,
        ))
        mailbox = self.by_key(result)["mailbox"]

        self.assertFalse(result["ready"])
        self.assertEqual(mailbox["status"], "ERROR")
        self.assertTrue(mailbox["required"])
        self.assertIn("cloudflare_api_key", mailbox["message"])

    def test_cloudflare_without_auth_does_not_require_an_api_key(self):
        result = preflight.run_health_checks(self.config(
            email_provider="cloudflare",
            cloudflare_api_base="https://mail.example",
            cloudflare_auth_mode="none",
            cloudflare_api_key="",
            cpa_export_enabled=False,
        ))
        self.assertEqual(self.by_key(result)["mailbox"]["status"], "OK")

    def test_malformed_urls_return_structured_results_instead_of_raising(self):
        proxy = preflight.run_health_checks(self.config(
            proxy_mode="pool",
            proxy_pool_subscription_url="http://[",
            cpa_export_enabled=False,
        ))
        self.assertEqual(self.by_key(proxy)["proxy"]["status"], "ERROR")
        self.assertTrue(self.by_key(proxy)["proxy"]["required"])

        grok2api = preflight.run_health_checks(self.config(
            cpa_export_enabled=False,
            grok2api_auto_add_remote=True,
            grok2api_remote_base="http://[",
            grok2api_remote_app_key="app-key",
        ))
        self.assertTrue(grok2api["ready"])
        self.assertEqual(self.by_key(grok2api)["grok2api"]["status"], "WARN")

        cloudflare = preflight.run_health_checks(self.config(
            email_provider="cloudflare",
            cloudflare_api_base="http://[",
            cpa_export_enabled=False,
        ))
        self.assertFalse(cloudflare["ready"])
        self.assertEqual(self.by_key(cloudflare)["mailbox"]["status"], "ERROR")

        cpa = preflight.run_health_checks(self.config(
            cpa_export_enabled=True,
            cpa_base_url="https://cpa.example:bad",
        ))
        self.assertTrue(cpa["ready"])
        self.assertEqual(self.by_key(cpa)["cpa"]["status"], "WARN")

    def test_summary_counts_only_required_errors_as_blocking(self):
        result = preflight.run_health_checks(self.config(cpa_export_enabled=False))
        self.assertTrue(result["ok"])
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["warning_count"], 0)
        self.assertIn("checked_at", result)


if __name__ == "__main__":
    unittest.main()
