# OpenCode V2: connection-only probe (2026-11-10)

Status: experimental; no inference, no new stable-release claim.
This probe supersedes the OpenCode configuration phase of
`scripts/pilot_opencode_managed.py`. It does not repeat its A->B lifecycle test.

## Evidence and diagnosis

The owner reported OpenCode 2.2.5, a successful A->B lifecycle check and the
single OpenCode log line `No MCP servers configured`. That line is NOT proof
of a broken memory engine or an inference attempt. In upstream v2.2.5,
`mcp list` prints it when the returned server list is empty.

The old pilot used `OPENCODE_CONFIG=/workspace/opencode.json` and put only
update/share settings in the managed file. Official V2 documentation says
legacy OPENCODE_* configuration environment variables are ignored. This is
a confirmed incompatibility in the old pilot, but the log alone does not
prove why workspace discovery also yielded no servers.

The targeted probe removes that ambiguity: the FULL V2 `mcp.servers` config
is mounted read-only at `/etc/opencode/opencode.json` INSIDE the sandbox.
Upstream discovery explicitly loads that directory on Linux. Nothing is
written to the host /etc or the owner's ordinary OpenCode settings.

## Boundaries

- Executes only `opencode mcp list`, no chat/run/model command.
- No calls to Ollama and no downloads or installations.
- Keeps network namespace isolation, private HOME, scoped broker and supervised closure.
- Mounts only the explicit binary, Python venv, module source, data socket and token.
- No whole HOME, SQLite, admin socket or administrative credential is mounted.
- The broker grants READ only, for a new synthetic project and temporary database.
- No lifecycle PASS is invented: the new summary reports lifecycle NOT_RUN.
- Success requires exit 0, the exact server marked connected AND a new broker
  profile request from the client, excluding the launcher's own preflight.
- Failure records an exit code and a diagnostic; a zero exit alone is not PASS.

## Run once from the existing repository venv

```bash
PYTHONPATH=src .venv/bin/python scripts/probe_opencode_mcp.py \
  --opencode "$HOME/.opencode/bin/opencode"
```

Temporary evidence directory: `symem-opencode-only-*`, outside the repo.
Expected success line (not a result already obtained on the owner's machine):

```text
PASS: OpenCode real conectado a MemorY por MCP dentro de Bubblewrap
```

## Verification of this change

15 focused configuration/argument/evidence unit tests PASS locally, once.
These do not execute OpenCode, Bubblewrap or a model. Native OpenCode end-to-end
connection remains unconfirmed until the owner executes the new probe.
Core memory, permissions, lifecycle controller, main and rc1 remain unchanged.

## Primary sources

- https://opencode.ai/docs/config/
- https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/commands/handlers/mcp/list.ts
- https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/config/discovery.ts
- https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/config.ts
- https://github.com/anomalyco/opencode/blob/v2.2.5/packages/schema/src/config/mcp.ts
- https://github.com/anomalyco/opencode/blob/v2.2.5/packages/schema/src/mcp.ts
