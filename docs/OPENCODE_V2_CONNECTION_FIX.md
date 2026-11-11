# OpenCode 2.2.5: connection remains unconfirmed

Updated 2026-11-10. This document corrects the earlier diagnosis.

## Evidence supplied by the owner

The original lifecycle A->B test passed. Both OpenCode probes returned
`No MCP servers configured`. For the c2abf86 probe the owner supplied:

- opencode_exit_code: 0
- broker_profiles_before_client: 1 (trusted launcher preflight)
- broker_profiles_from_client: 0
- sandbox_closed: true
- token_revoked: true

This proves no successful profile request arrived from the client. It does
NOT prove why the server list is empty. `CONFIG_NOT_LOADED` is our old
classification label, not an OpenCode diagnostic or a confirmed root cause.

## Correction to the earlier explanation

The assertion that OpenCode V2 ignores OPENCODE_CONFIG was too broad and is
incorrect for the examined v2.2.5 source: packages/cli/src/server-process.ts
forwards OPENCODE_CONFIG as config.file. The historical commit message and
comment in probe_opencode_mcp.py should NOT be treated as a verified cause.
Changing only the managed config location did not resolve the owner's run.
No new configuration-path fix is claimed here.

## Targeted diagnostic, not another blind configuration change

Run `scripts/diagnose_opencode_config.py` with the existing venv. It reuses the
same managed configuration and runtime mount list as the previous probe.
Inside a fresh Bubblewrap namespace it:

1. Reads the mounted config and verifies its SHA-256 against the host fixture.
2. Checks importability of the already installed MCP and MemorY Python modules.
3. Runs the installed OpenCode binary with --version, then --standalone
   --print-logs debug config, then --standalone --print-logs mcp list.
4. Captures configuration source paths and recognized MCP server names,
   plus relevant server warnings. Raw transient output is private; known
   fixture secrets and opaque credential-like values are redacted in retained logs.
5. Closes the namespace, revokes its read-only fixture grant and prints the
   diagnostic summary directly, so a second cat command is not normally needed.

Standalone uses a private OpenCode service, not the background service mode
of the earlier probe. This is an explicit diagnostic variable, not proof that
the previous run contacted a host service. Host HOME/state remain unmounted.
Network namespace isolation stays enabled. No model, prompt, Ollama call,
download, installation, host configuration or real repository is used.
The A->B lifecycle test is NOT repeated. No change to core memory/controller.

`CONNECTED_IN_STANDALONE_DIAGNOSTIC` requires matching mounted config,
a working Python runtime, the server present in OpenCode's reported config,
a connected label from mcp list, actual broker profile activity from the
client and successful supervised closure/revocation. It is not v1 certification
or evidence of inference. Other outcomes identify the failed diagnostic stage.

17 new pure helper tests PASS locally (command allowlist, classifications,
config filtering and redaction). They do NOT execute OpenCode or Bubblewrap.
Native diagnostics remain pending on the owner's machine.

## Version-pinned primary sources

- https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/commands/commands.ts
- https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/commands/handlers/debug/config.ts
- https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/services/standalone.ts
- https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/server-process.ts
- https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/config/discovery.ts
- https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/config/normalize.ts
