"""Coordinate primary SQLite persistence with the legacy TXT export."""


def persist_account(database, output_path, account, email_provider, registration_run_id):
    account_id, inserted = database.insert_account({
        "email": account.email,
        "password": account.password,
        "sso": account.sso,
        "email_provider": email_provider,
        "registration_status": "registered",
        "authorization_status": "unknown",
        "source": "native",
        "profile": account.profile,
        "registration_run_id": registration_run_id,
    })
    legacy_export_error = ""
    try:
        from account_outputs import ensure_account_line

        ensure_account_line(output_path, account.email, account.password, account.sso)
    except Exception as exc:
        legacy_export_error = str(exc)
    return {
        "account_id": account_id,
        "inserted": inserted,
        "legacy_export_error": legacy_export_error,
    }


def persist_account_status(database, account_id, pools, cpa, password="", sso=""):
    def status_for_pool(name):
        state = pools.get(name, {})
        if not state.get("enabled"):
            return "not_requested", ""
        if state.get("ok") is True:
            return "succeeded", ""
        if state.get("ok") is False:
            error = str(state.get("error") or "")
            for secret in (password, sso):
                if secret:
                    error = error.replace(secret, "[REDACTED]")
            return "failed", error[:500]
        return "unknown", ""

    if not cpa or cpa.get("skipped"):
        cpa_status, cpa_error = "not_requested", ""
    elif cpa.get("ok"):
        cpa_status = "warning" if cpa.get("warning") or cpa.get("cpa_copy_error") else "succeeded"
        cpa_error = str(cpa.get("cpa_copy_error") or "")
    else:
        cpa_status = "failed"
        cpa_error = str(cpa.get("error") or "")

    for secret in (password, sso):
        if secret:
            cpa_error = cpa_error.replace(secret, "[REDACTED]")

    local_status, local_error = status_for_pool("local")
    remote_status, remote_error = status_for_pool("remote")
    return database.update_account_integration_status(
        account_id,
        cpa_status,
        cpa_error[:500],
        local_status,
        local_error,
        remote_status,
        remote_error,
    )
