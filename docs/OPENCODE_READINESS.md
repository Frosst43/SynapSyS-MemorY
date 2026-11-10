# OpenCode 2.2.5: keep one service alive during MCP readiness

2026-11-10. Experimental diagnostic change; no stable release declaration.

## Evidence supplied by the owner

The preceding diagnostic verified the exact mounted configuration, successful
Python imports, and `synapsys-memory` in OpenCode's parsed managed document.
A separate standalone `mcp list` still returned an empty list with zero broker
profile requests. Namespace closure and grant revocation succeeded.
This rules out the prior claim that the managed configuration was not read.

## Source-supported hypothesis, not yet a confirmed root cause

In upstream v2.2.5:
- `packages/core/src/plugin/supervisor.ts` forks initial plugin activation.
- `packages/core/src/config/plugin/mcp.ts` registers config-defined servers.
- `packages/core/src/mcp/index.ts`, `MCP.servers`, reads the current entries
  without awaiting configuration-plugin activation.
- `packages/server/src/handlers/mcp.ts` calls that snapshot directly.
- Our preceding diagnostic used two separate `--standalone` commands: config
  discovery and MCP listing did not observe the same running server instance.

This creates a plausible startup timing failure in the test. The owner's log
alone does not prove that timing is the only remaining problem.

## Targeted change

`scripts/probe_opencode_readiness.py` preserves the same configuration and
sandbox mounts. It starts exactly one private `opencode serve --stdio --port 0`
inside the existing network/PID namespace, keeps its stdin lease open, and
uses `--server` for configuration and bounded read-only MCP status queries.
No restart loop, schema guessing, broad HOME mount or host networking fallback.

The loopback HTTP endpoint belongs to the isolated namespace, not to the host
or LAN. Its ephemeral password is generated inside the sandbox and sent via
environment, never in command arguments or retained logs. Model catalogue
fetching is disabled; no model or inference command is invoked.

A positive result requires: exact config, usable Python runtime, recognized
server name, one service start, connected status, a client-originated broker
profile request, namespace closure and token revocation. The launcher profile
is excluded. `stable_release_ready` remains false.

The script prints its bounded state samples and relevant failure logs itself.
An empty initial sample is not success, but neither is it prematurely final.
A failed/disabled connection or command error stops polling. Maximum 16 reads
within a 15-second polling budget. These are reads of one service, not repeated
A->B tests or repeated memory writes.

## Validation

20 focused helper tests PASS locally, one run. They cover loopback URL
validation, command allowlisting, polling bounds, same-service reuse, terminal
failures and rejection of false-positive connection results. They do not run
OpenCode, Bubblewrap or inference. Native end-to-end validation is pending.

```bash
PYTHONPATH=src .venv/bin/python scripts/probe_opencode_readiness.py \
  --opencode "$HOME/.opencode/bin/opencode"
```

Expected positive summary: `CONNECTED_IN_SAME_SERVICE`. Anything else remains
unconfirmed and does not weaken isolation. No production core files changed.

Primary source root (all paths above pinned to v2.2.5):
https://github.com/anomalyco/opencode/tree/v2.2.5
Private service lease and authentication:
https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/services/standalone.ts
https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/server-process.ts
