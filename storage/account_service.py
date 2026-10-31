"""Coordinate primary SQLite persistence with the legacy TXT export."""

from account_outputs import ensure_account_line


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
        ensure_account_line(output_path, account.email, account.password, account.sso)
    except Exception as exc:
        legacy_export_error = str(exc)
    return {
        "account_id": account_id,
        "inserted": inserted,
        "legacy_export_error": legacy_export_error,
    }
