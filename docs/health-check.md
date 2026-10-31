# Dependency health checks

Grok Register provides one unified health report for Configuration, Browser Runtime, Proxy, Mailbox, CPA, and Grok2API.

## Run from CLI

Static checks are offline and do not launch a browser or contact external services:

\`\`\`bash
python grok_register_ttk.py check
\`\`\`

Explicitly run connection checks:

\`\`\`bash
python grok_register_ttk.py check --connectivity
\`\`\`

The command exits with code \`0\` when all required dependencies pass, and \`1\` when a required check fails.

## GUI and WebUI

- GUI: use **静态依赖检查** or **联网依赖检查**.
- WebUI: use **Static check** or **Connectivity check** in the top bar.
- API: \`POST /api/health-check\` with \`{"connectivity": false}\` for static checks or \`{"connectivity": true}\` for user-triggered connection checks.

The endpoint is same-origin only, returns \`Cache-Control: no-store\`, and is rejected while a registration or maintenance task is running.

## Status semantics

Each item is \`OK\`, \`WARN\`, or \`ERROR\` and identifies whether it is required or optional.

- A required \`ERROR\` changes the overall result to \`Needs attention\`.
- An optional \`WARN\` does not block the overall \`Ready\` result. CPA and Grok2API are optional post-processing integrations.
- Static checks validate local configuration, installed dependencies, local files, and directory availability. They do not perform network requests.
- Connectivity checks run only after an explicit user action. They test browser startup, proxy reachability, the selected mailbox provider, and configured optional service endpoints.
- CPA connectivity checks do not start OAuth authorization. Grok2API connectivity checks verify endpoint reachability but do not validate remote credentials.
- The health check never starts registration, creates a mailbox, imports a token, or writes account data. It is not automatically run before registration.

The report contains no mailbox passwords, refresh tokens, API keys, proxy credentials, or access tokens.
