"""验证共享注册流程的重试、统计、取消、清理和后处理边界。"""

import unittest

from registration_flow import (
    RecoverablePreSubmitError,
    RegistrationCallbacks,
    RegistrationOperations,
    RegistrationResult,
    persist_account_result,
    run_batch,
)


class Cancelled(Exception):
    pass


class Retryable(Exception):
    pass


class FakeOps:
    def __init__(self, save_ok=True, observer_events=None):
        self.events = []
        self.save_ok = save_ok
        self.observer_events = observer_events if observer_events is not None else []
        self.account_no = 0

    def operations(self):
        return RegistrationOperations(
            start_browser=lambda: self.events.append("start"),
            restart_browser=lambda: self.events.append("restart"),
            browser_missing=lambda: False,
            open_signup_page=lambda: self.events.append("open"),
            fill_email_and_submit=self._email,
            save_mail_credential=lambda email, token: True,
            fill_code_and_submit=lambda email, token: "123456",
            fill_profile_and_submit=lambda: {"given_name": "A", "family_name": "B", "password": "pw"},
            wait_for_sso_cookie=lambda: "sso-token",
            enable_nsfw=lambda sso: (True, "ok"),
            persist_account_line=self._persist,
            queue_unsaved_result=lambda payload, error: True,
            add_tokens=lambda sso, email: {
                "local": {"enabled": False, "ok": None, "error": None},
                "remote": {"enabled": False, "ok": None, "error": None},
            },
            export_cpa=lambda email, password, sso: {"ok": False, "skipped": True},
            cleanup=lambda reason: self.events.append(("cleanup", reason)),
            sleep=lambda seconds: self.events.append(("sleep", seconds)),
            cancelled_exception=Cancelled,
            retry_exception=Retryable,
        )

    def _email(self):
        self.account_no += 1
        return f"user{self.account_no}@example.com", "mail-token"

    def _persist(self, email, password, sso):
        if not self.save_ok:
            raise OSError("disk full")
        self.events.append(("persist", email))


class RegistrationFlowTests(unittest.TestCase):
    def callbacks(self, logs=None):
        logs = logs if logs is not None else []
        return RegistrationCallbacks(log=logs.append, cancelled=lambda: False)

    def test_start_failure_still_runs_cleanup(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.start_browser = lambda: (_ for _ in ()).throw(RuntimeError("start failed"))
        with self.assertRaises(RuntimeError):
            run_batch(1, self.callbacks(), lambda *args: None, ops)
        self.assertEqual(fake.events, [("cleanup", "任务结束")])

    def test_last_account_does_not_restart_browser(self):
        fake = FakeOps()
        batch = run_batch(1, self.callbacks(), lambda *args: None, fake.operations())
        self.assertEqual(batch.success_count, 1)
        self.assertNotIn("restart", fake.events)
        self.assertEqual(fake.events[-1], ("cleanup", "任务结束"))

    def test_cleanup_interval_does_not_repeat_after_unsaved_result(self):
        fake = FakeOps(save_ok=True)
        ops = fake.operations()
        original_persist = ops.persist_account_line
        calls = {"count": 0}

        def persist(email, password, sso):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("disk full")
            original_persist(email, password, sso)

        ops.persist_account_line = persist
        batch = run_batch(2, self.callbacks(), lambda *args: None, ops, cleanup_interval=1)
        interval_cleanups = [
            event for event in fake.events
            if isinstance(event, tuple)
            and len(event) > 1
            and isinstance(event[1], str)
            and "已成功" in event[1]
        ]
        self.assertEqual(len(interval_cleanups), 1)
        self.assertEqual(batch.success_count, 1)
        self.assertEqual(batch.registered_unsaved_count, 1)

    def test_observer_failure_is_logged_and_batch_continues(self):
        fake = FakeOps()
        logs = []

        def broken_observer(*args):
            raise RuntimeError("ui broke")

        batch = run_batch(1, self.callbacks(logs), broken_observer, fake.operations())
        self.assertEqual(batch.success_count, 1)
        self.assertTrue(any("observer 执行失败" in line for line in logs))

    def test_cleanup_failure_does_not_change_success_statistics(self):
        fake = FakeOps()
        ops = fake.operations()
        def cleanup(reason):
            if "已成功" in reason:
                raise RuntimeError("cleanup failed")
            fake.events.append(("cleanup", reason))
        ops.cleanup = cleanup
        batch = run_batch(2, self.callbacks(), lambda *args: None, ops, cleanup_interval=1)
        self.assertEqual(batch.success_count, 2)
        self.assertEqual(batch.fail_count, 0)
        self.assertEqual(batch.processed_count, 2)

    def test_cancel_during_between_account_sleep_ends_normally(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.sleep = lambda seconds: (_ for _ in ()).throw(Cancelled())
        batch = run_batch(2, self.callbacks(), lambda *args: None, ops)
        self.assertTrue(batch.cancelled)
        self.assertEqual(batch.success_count, 1)
        self.assertEqual(batch.processed_count, 1)

    def test_final_cleanup_failure_does_not_hide_original_error(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.start_browser = lambda: (_ for _ in ()).throw(RuntimeError("original start error"))
        ops.cleanup = lambda reason: (_ for _ in ()).throw(RuntimeError("cleanup error"))
        logs = []
        with self.assertRaisesRegex(RuntimeError, "original start error"):
            run_batch(1, self.callbacks(logs), lambda *args: None, ops)
        self.assertTrue(any("清理失败" in line for line in logs))

    def test_postprocessing_exceptions_become_warnings(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.add_tokens = lambda sso, email: (_ for _ in ()).throw(RuntimeError("pool down"))
        ops.export_cpa = lambda email, password, sso: (_ for _ in ()).throw(RuntimeError("cpa down"))
        batch = run_batch(1, self.callbacks(), lambda *args: None, ops)
        self.assertEqual(batch.success_count, 1)
        self.assertEqual(batch.fail_count, 0)
        self.assertEqual(batch.postprocess_warning_count, 1)


    def test_attempt_callback_records_retry_and_success(self):
        fake = FakeOps()
        ops = fake.operations()
        starts = {"count": 0}

        def start_browser():
            starts["count"] += 1
            if starts["count"] == 1:
                raise RecoverablePreSubmitError("temporary navigation failure")

        ops.start_browser = start_browser
        attempts = []
        callbacks = RegistrationCallbacks(
            log=lambda _message: None,
            cancelled=lambda: False,
            record_attempt=attempts.append,
        )
        batch = run_batch(1, callbacks, lambda *args: None, ops)

        self.assertEqual(batch.success_count, 1)
        self.assertEqual([item["status"] for item in attempts], ["retrying", "succeeded"])
        self.assertEqual([item["attempt_number"] for item in attempts], [1, 2])
        self.assertEqual([item["slot_index"] for item in attempts], [1, 1])
        self.assertEqual(attempts[0]["error_type"], "RecoverablePreSubmitError")
        self.assertEqual(attempts[1]["error_type"], "")

    def test_attempt_callback_records_failed_stage_without_credentials(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.start_browser = lambda: (_ for _ in ()).throw(RuntimeError("failed before registration"))
        attempts = []
        callbacks = RegistrationCallbacks(
            log=lambda _message: None,
            cancelled=lambda: False,
            record_attempt=attempts.append,
        )
        with self.assertRaisesRegex(RuntimeError, "failed before registration"):
            run_batch(1, callbacks, lambda *args: None, ops)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "failed")
        self.assertEqual(attempts[0]["stage"], "browser_start")

    def test_persist_account_status_receives_independent_results(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.persist_account_record = lambda account: {
            "account_id": 42,
            "legacy_export_error": "",
        }
        recorded = []
        ops.persist_account_status = lambda *args: recorded.append(args)
        ops.add_tokens = lambda sso, email: {
            "local": {"enabled": True, "ok": True, "error": ""},
            "remote": {"enabled": True, "ok": False, "error": "remote failed"},
        }
        ops.export_cpa = lambda email, password, sso: {
            "ok": True, "skipped": False, "warning": True,
        }

        output = persist_account_result(
            RegistrationResult(ok=True, email="user@example.com", password="pw", sso="token"),
            self.callbacks(),
            ops,
        )

        self.assertEqual(output.account_id, 42)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0][0], 42)
        self.assertEqual(recorded[0][1]["remote"]["ok"], False)
        self.assertTrue(recorded[0][2]["warning"])

    def test_attempt_history_includes_account_persistence_failure(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.persist_account_record = lambda _account: (_ for _ in ()).throw(OSError("disk full"))
        attempts = []
        callbacks = RegistrationCallbacks(
            log=lambda _message: None,
            cancelled=lambda: False,
            record_attempt=attempts.append,
        )

        batch = run_batch(1, callbacks, lambda *_args: None, ops)

        self.assertEqual(batch.registered_unsaved_count, 1)
        self.assertEqual(attempts[0]["status"], "registered_unsaved")
        self.assertEqual(attempts[0]["error_type"], "AccountPersistenceError")
        self.assertEqual(attempts[0]["error_message"], "disk full")

    def test_partial_cpa_export_counts_as_postprocess_warning(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.persist_account_record = lambda _account: {
            "account_id": 7,
            "legacy_export_error": "",
        }
        ops.persist_account_status = lambda *_args: True
        ops.export_cpa = lambda *_args: {
            "ok": True, "skipped": False, "partial": True,
        }

        batch = run_batch(1, self.callbacks(), lambda *_args: None, ops)

        self.assertEqual(batch.success_count, 1)
        self.assertEqual(batch.postprocess_warning_count, 1)

if __name__ == "__main__":
    unittest.main()
