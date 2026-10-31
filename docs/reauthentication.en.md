# Account reauthentication and authorization recovery

Existing accounts can be reauthenticated through the official sign-in page, with all job data persisted in SQLite. Only accounts with stored email and password are eligible. Any additional human verification remains manual; the workflow does not bypass platform challenges.

## Local WebUI API

- `POST /api/reauth/start`: start a sequential batch, for example `{"ids":[1,2]}`.
- `GET /api/reauth/status`: get the latest run and account-level results.
- `POST /api/reauth/stop`: request cancellation.
- `GET /api/reauth/history`: get persisted job history.
- `GET /api/reauth/jobs/{job_id}`: get a specific job.

PR-06 provides the backend; PR-08 will add the full React UI.

## Data handling

A fresh SSO cookie is required before the existing credential changes. The database updates the authorization status and authentication timestamp, and prevents re-importing the obsolete SSO from legacy TXT files.

Associated legacy `email----password----sso` files are atomically rewritten after a successful database update. If no file was associated with the account, `accounts_reauthenticated.txt` is used. Compatibility export failures appear as warnings and do not undo a valid new SQLite session.

Existing CPA and Grok2API functions are reused. Downstream failures are reported independently from sign-in success. Jobs and per-account outcomes are stored in `job_runs` and `job_accounts`; the latest job is accessible after a process restart through the history API.

## Validation

All tests use FakePage and Mock, with no live registration, account sign-in or external service calls. Actual third-party page compatibility is not established by these tests.
