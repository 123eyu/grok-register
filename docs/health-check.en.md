# Dependency health checks

Grok Register provides one health report for Configuration, Browser Runtime, Proxy, Mailbox, CPA, and Grok2API.

## CLI

Static checks are offline:

```bash
python grok_register_ttk.py check
```

Run connection checks only when explicitly requested:

```bash
python grok_register_ttk.py check --connectivity
```

The command exits with code `0` when all required checks pass and `1` when a required check fails.

## GUI and WebUI

- GUI: click **静态依赖检查** or **联网依赖检查**.
- WebUI: click **Static check** or **Connectivity check**.
- API: `POST /api/health-check` with `{"connectivity": false}` or `{"connectivity": true}`.

The API is same-origin only, returns `Cache-Control: no-store`, and is rejected while registration or another maintenance operation is running.

## Status semantics

Each check is `OK`, `WARN`, or `ERROR`, and identifies whether the dependency is required or optional.

- A required `ERROR` changes the overall result to `Needs attention`.
- An optional `WARN` does not block `Ready`. CPA and Grok2API are optional post-processing integrations.
- Static checks inspect local configuration, installed dependencies, local files, and directory availability. They do not make network requests.
- Connectivity checks run only after an explicit user action. They check browser startup, proxy reachability, the selected mailbox provider, and enabled optional service endpoints.
- CPA connectivity checks do not start OAuth authorization. Grok2API checks endpoint reachability but do not validate remote credentials.
- The health check never starts registration, creates a mailbox, imports a token, or writes account data. It is not automatically run before registration.

The report does not include mailbox passwords, refresh tokens, API keys, proxy credentials, or access tokens.
