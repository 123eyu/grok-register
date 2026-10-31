"""Sequential reauthentication job coordinator and authorization recovery."""

import os
from pathlib import Path
import threading

from account_outputs import replace_account_session
from reauthentication import LoginCancelled
from storage.account_service import persist_account_status


class ReauthManager:
    def __init__(self, database, login, postprocess, output_dir, log=lambda _text: None, on_finish=None):
        self.database = database
        self.login = login
        self.postprocess = postprocess
        self.output_dir = Path(output_dir)
        self.log = log
        self.on_finish = on_finish
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._job_id = None

    def start(self, account_ids):
        ids = list(dict.fromkeys(int(item) for item in account_ids))
        if not ids or any(item < 1 for item in ids):
            raise ValueError("请选择有效的账号")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("已有重新登录任务正在运行")
            accounts = [self.database.get_account(item) for item in ids]
            if any(record is None for record in accounts):
                raise LookupError("所选账号存在已删除的记录")
            job_id = self.database.create_job("reauthentication", accounts)
            self._stop.clear()
            self._job_id = job_id
            thread = threading.Thread(
                target=self._run, args=(job_id, accounts),
                name="grok-register-reauthentication", daemon=True,
            )
            self._thread = thread
            thread.start()
            return job_id

    def status(self):
        with self._lock:
            job_id = self._job_id
            running = self._thread is not None and self._thread.is_alive()
        if job_id is None:
            return {"running": False, "job": None}
        return {"running": running, "job": self.database.get_job(job_id)}

    def stop(self):
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            if running:
                self._stop.set()
            return running

    def _restore_legacy_exports(self, account, old_sso, new_sso):
        paths = {
            item["path"] for item in account["artifacts"]
            if item["artifact_type"] == "legacy_txt"
        }
        if not paths:
            paths = {str(self.output_dir / "accounts_reauthenticated.txt")}

        errors = []
        for path in sorted(paths):
            try:
                replace_account_session(
                    path, account["email"], account["password"], old_sso, new_sso
                )
                self.database.add_artifact(account["id"], "legacy_txt", os.path.realpath(path), "txt")
            except Exception as exc:
                errors.append("TXT 同步失败: " + type(exc).__name__)
        return errors

    def _run(self, job_id, accounts):
        try:
            for account in accounts:
                account_id = account["id"]
                if self._stop.is_set():
                    self.database.update_job_item(job_id, account_id, "skipped")
                    continue
                if not account["password"]:
                    self.database.update_job_item(job_id, account_id, "failed", "没有保存密码")
                    continue

                self.database.update_job_item(job_id, account_id, "running")
                try:
                    new_sso = self.login(
                        account["email"], account["password"],
                        cancelled=self._stop.is_set, log=self.log,
                    )
                    if self._stop.is_set():
                        raise LoginCancelled("重新登录已停止")
                    previous = self.database.update_account_session(account_id, new_sso)
                    warnings = self._restore_legacy_exports(account, previous["old_sso"], new_sso)
                    try:
                        integrations = self.postprocess(
                            account["email"], account["password"], new_sso,
                            cancelled=self._stop.is_set, log=self.log,
                        )
                        self.database.update_account_integration_status(
                            account_id, *integrations
                        )
                    except Exception as exc:
                        warnings.append("授权后处理失败: " + type(exc).__name__)
                    self.database.update_job_item(
                        job_id, account_id, "succeeded", "; ".join(warnings)
                    )
                except LoginCancelled:
                    self.database.update_job_item(job_id, account_id, "skipped")
                    self._stop.set()
                except Exception as exc:
                    self.database.update_job_item(
                        job_id, account_id, "failed", type(exc).__name__ + ": " + str(exc)[:200]
                    )
            self.database.finish_job(
                job_id, "cancelled" if self._stop.is_set() else "completed",
                cancelled=self._stop.is_set(),
            )
        except Exception as exc:
            self.database.finish_job(job_id, "failed", error_message=type(exc).__name__)
        finally:
            if self.on_finish is not None:
                self.on_finish()
