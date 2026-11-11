# SQLite persistence and OpenCode MCP pilots

The experimental persistence pilot exercises `src/synapsys_memory/broker.py` against
`~/.local/state/synapsys-memory/memory.sqlite3`. If an existing DB is private,
it is reused without replacing it. No user project is granted: only the
synthetic `symem-validation/persistence-pilot` scope is written.

The pilot creates a synthetic memory, revokes writer rights, stops the broker,
starts a fresh broker with a new admin secret, recovers the memory as a
different read-only client, checks FTS5 search, verifies another project
cannot read it, and proves the read-only client cannot write. It revokes the
new grants and stops the second broker. Both broker instances run in the
same Python process. The synthetic marker stays in SQLite for future
explicitly authorized validation.

The SQLite pilot runs offline with ephemeral tokens and synthetic data. It
writes a credential-free summary in
`~/.local/state/synapsys-memory/validaciones/`.
The test refuses symlinked/unsafe files, open directories, and a broker socket
in the default location. It does not chmod or replace existing databases.

## Directory permissions

The SQLite parent must have a canonical path, belong to the invoking UID, and
have mode `0700`. An existing SQLite file must be a regular file with a
single hard link, belong to that UID, and have mode `0600`. Both the pilot
and the broker reject mode `0775` on the SQLite parent.

Inspect existing paths with `namei -l` and `stat`. If the default SQLite
parent has a canonical path and the invoking UID owns it, tighten an overly
broad mode with `chmod 700 -- "$HOME/.local/state/synapsys-memory"`.

When saving evidence, apply the same directory checks to any existing
`validaciones/` before running. The pilot checks it after the write, so
inspect the synthetic scope for an existing record before retrying a failure
at that step.

## Run on the target computer

Run the pilot once per validation.

```bash
cd "$HOME/Documentos/SynapSyS-MemorY"
PYTHONPATH=src .venv/bin/python scripts/verify_persistent_memory.py
```

A local-only temporary test is available with `--db /path/to/private/test.sqlite3`.
Do not pass a DB path inside Git checkout. Source code and unit tests belong on
GitHub; the SQLite database and its markers belong only to the user's machine.

## Validation on Kubuntu

After the directory permission correction, the pilot saved and recovered one
synthetic record. A fresh OS process running `synapsys-memory-broker serve`
then reopened the same SQLite file. A trusted MCP SDK client called
`memory_get` and `memory_recall` over stdio with a read-only grant. Both calls
returned the record in the pilot scope and returned no match in the other
project. After grant revocation and a clean SIGTERM shutdown, SQLite passed
`integrity_check` and the file matched its bytes before the MCP check.

### Supervised broker restart (PASS)

A transient `systemd --user` unit restarted the broker once after `SIGKILL`.
The new PID was ready in 0.798 seconds. The broker recovered the committed
synthetic record from the WAL by ID and through FTS5. SQLite passed
`integrity_check`.

Both stale socket files were present after the crash and refused connections.
With `RuntimeDirectoryPreserve=no`, systemd removed the runtime directory and
the broker bound new sockets. Both old tokens were rejected before and after
reissuing grants for the same sessions. The unit and disposable database were
removed after the test.
[systemd runtime directory cleanup](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#RuntimeDirectoryPreserve=)

### OpenCode `memory_get` in Bubblewrap (PASS)

OpenCode 2.2.5's `Mcp.Service.callTool` issued one `memory_get` over MCP stdio.
The response matched the synthetic record's ID, content, scope, and revision.
The broker observed one read with a read-only grant. The test used a private
copy of the pilot database and verified that the original file stayed
byte-for-byte identical.
Bubblewrap isolated the network and kept SQLite and the admin socket on the
host. The test revoked the grant and closed the sandbox.
[OpenCode 2.2.5 MCP tool dispatch](https://github.com/anomalyco/opencode/blob/9bbb8ce18c80f099e0e448775f59d965ead40a21/packages/core/src/mcp/index.ts#L819)

### Reproduce the OpenCode read

From the checkout root, use the repository's `.venv` with the MCP extra,
Bubblewrap on Linux, and the native OpenCode 2.2.5 executable with bundled Bun
1.4.4.

```bash
PYTHONPATH=src .venv/bin/python -B scripts/verify_opencode_memory_get.py
```

Use `--opencode /path/to/opencode` for an executable outside
`~/.opencode/bin/opencode`. Preparation fetches the pinned OpenCode source
from GitHub and its locked dependencies from the package registry into a
temporary directory with install scripts disabled. The call runs in a
separate network namespace using Bun from the installed binary, without an
agent session or model.

The script seeds a fresh disposable SQLite database and grants only read
access to the sandbox. A successful run emits a JSON summary with
`"result": "PASS"` and `"tool": "memory_get"` after verifying the returned
record, one broker read, revocation, and cleanup. It exits nonzero if the
verification fails.

## Broker user service

The [example systemd user unit](../examples/systemd/synapsys-memory-broker.service)
uses `~/.local/state/synapsys-memory/memory.sqlite3` for persistent data.
Its sockets are in `$XDG_RUNTIME_DIR/synapsys-memory-broker/`, which systemd
recreates on restart. Clients receive the data socket and their own
project-scoped token. Keep the admin socket and key in the trusted
controller. [systemd runtime directory cleanup](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#RuntimeDirectoryPreserve=)

The unit reads the admin key from a private file under the user manager's
configuration directory. The CLI option `--admin-key-file` takes precedence
over `SYMEM_ADMIN_KEY` and rejects an unsafe or invalid file. The environment
variable remains supported for manual commands. To provision a fresh key,
run the following once. Exclusive creation preserves an existing key file.

```bash
(
  umask 077
  symem_config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/synapsys-memory"
  mkdir -p -- "$symem_config_dir"
  python3 - "$symem_config_dir/broker-admin.key" <<'PY'
from pathlib import Path
import secrets
import sys

with Path(sys.argv[1]).open("x", encoding="ascii") as key_file:
    key_file.write(secrets.token_urlsafe(48) + "\n")
PY
)
```

Place the example unit in the user manager's `systemd/user/` configuration
directory, normally `~/.config/systemd/user/`. Set the executable at the
start of `ExecStart` to the absolute path of the existing broker executable
when it lives in the repository's `.venv`. Confirm the SQLite directory and
file permissions above, and stop any other broker using that database before
starting the unit.

```bash
systemctl --user daemon-reload
systemctl --user start synapsys-memory-broker.service
```

After the admin socket appears, check the service from the checkout root.

```bash
.venv/bin/synapsys-memory-broker \
  --admin-key-file "${XDG_CONFIG_HOME:-$HOME/.config}/synapsys-memory/broker-admin.key" \
  --admin-socket "$XDG_RUNTIME_DIR/synapsys-memory-broker/admin.sock" \
  sessions
```

Stop the unit with `systemctl --user stop synapsys-memory-broker.service`.
SQLite persists across stops. A restarted broker requires fresh client grants.
Use `systemctl --user enable synapsys-memory-broker.service` to start it with
future user sessions when automatic startup is desired.

On Kubuntu, the example passed unit syntax verification and a transient test
with a disposable database and key. The CLI authenticated from the key file,
the service had no admin key in its arguments or environment, and a
`SIGKILL` triggered one restart. The broker recovered the committed record by
ID and FTS5, recreated both sockets, and rejected the previous token. A
normal stop removed the runtime directory and left the temporary SQLite file
intact until test cleanup.

The installed user unit also passed startup, supervised recovery, and clean
stop with the persistent database. A temporary read-only grant recovered the
existing synthetic record after `SIGKILL`. SQLite passed integrity checking
and kept the same inode and SHA-256. The service was left active and enabled.

## Owner approval for project changes

Use the installed user broker and an owner-private policy file with mode
`0600` outside the registered workspaces. The policy registers disjoint
workspace paths and their filesystem access.

```json
{
  "schema_version": 1,
  "projects": {
    "project-a": {"workspace": "/absolute/path/to/project-a", "read_only": true},
    "project-b": {"workspace": "/absolute/path/to/project-b", "read_only": true}
  }
}
```

For generated directories, a project can add `"exclude": ["build"]`.
Each name must refer to an existing directory directly below the workspace.
Bubblewrap replaces those directories with empty read-only mounts before
starting the client. Their contents stay on the host. The rest of the
workspace keeps its configured access mode and the usual audit limit.
Nested mounts are rejected anywhere in the workspace. A hardlink is allowed
only when every link to its inode is present in the audited files.

From the checkout root, run the controller in the owner's foreground terminal.
The command after `--` is used in each chosen workspace. This example runs a
disposable background process.

```bash
PYTHONPATH=src .venv/bin/python -B -m synapsys_memory.controller_cli \
  --policy /absolute/path/to/projects.json --space personal \
  -- /usr/bin/sleep 300
```

Enter a project ID from the policy. Answer `s` to approve the activation or
switch. Answer `n` or press Enter to deny it and keep the current sandbox
and grant. On approval, the controller revokes the old grant and confirms
that the PID namespace has closed before issuing the new grant. Enter
`:salir` at the project prompt to revoke the current grant and close its
sandbox.

The controller reads decisions from `/dev/tty` on the host. For background
commands it routes input and output to `/dev/null`. The controller grants
read access to memory by default. The owner can explicitly set broker
permissions with `--rights`. The policy's `read_only` setting controls
workspace files.

The terminal flow passed with the installed broker and two disposable
workspaces. Scripted input through a host pseudoterminal denied B and
confirmed that A's sandbox, detached descendant, and token were still
active. A subsequent approval revoked A and closed its namespace before
the broker issued B's read-only grant. Exit revoked B. The broker stayed
running and SQLite stayed byte-for-byte identical.

## OpenCode under the owner controller

Pass the path to the native OpenCode 2.2.5 executable with `--opencode` and
use `/opt/symem-pilot/opencode` as its path in the sandbox. The controller
mounts the executable, the checkout's `.venv` and `src`, and its generated
OpenCode configuration read-only. Each approved sandbox gets the broker's
data socket and a fresh token for that project. Bubblewrap isolates the
network and provides temporary HOME and XDG directories. The broker's admin
socket, key, and database stay on the host.

For a writable disposable workspace, save the native CLI's configuration
report inside that workspace. The report should include `synapsys-memory`
from `/etc/opencode/opencode.json`.

```bash
PYTHONPATH=src .venv/bin/python -B -m synapsys_memory.controller_cli \
  --policy /absolute/path/to/projects.json --space pilot --rights read \
  --opencode "$HOME/.opencode/bin/opencode" \
  -- /usr/bin/sh -c 'umask 077; set -C
    exec /opt/symem-pilot/opencode --standalone debug config > /workspace/opencode-config.json'
```

### OpenCode project switch with the installed broker (PASS)

The native CLI discovered the managed MCP server in Bubblewrap. A separate
end-to-end switch used two disposable workspaces and the pinned OpenCode
core probe from `scripts/verify_opencode_memory_get.py` with bundled Bun.
OpenCode issued `memory_get` over MCP stdio in A and recovered the existing
synthetic record from the installed broker with a read-only token. After
owner approval, the broker rejected A's token and the controller closed
A's PID namespace before issuing B's read-only token. The same OpenCode
call for the same ID succeeded in B with a `null` result. Both tool catalogs
exposed only read tools. Exiting the controller revoked B and closed its
namespace.

The broker stayed active with the same PID. SQLite kept its inode and bytes,
had zero WAL data frames, and passed `integrity_check`. The probe called
OpenCode's MCP service directly with no agents or model inference.

### Interactive OpenCode startup in Bubblewrap (PASS)

The controller launched native OpenCode 2.2.5 for a disposable project with
the installed broker. A separate pseudoterminal inside the sandbox let the
test open the TUI's Status panel with `Ctrl+X`, then `s`. The panel displayed
`synapsys-memory Connected`, and the MCP process was alive in the sandbox.
The broker confirmed the active project's read-only grant. The test used no
prompts, agents, or model inference.

The controller's approval terminal stayed separate from the TUI. The test
harness supplied the client pseudoterminal. Exiting through the controller
revoked the grant and closed the sandbox. The broker PID and SQLite file
hash stayed the same, and the WAL contained zero data frames.

### Use a separate terminal for OpenCode

From the checkout root, run this in the terminal that will display OpenCode.
Leave it running and copy the socket path it prints.

```bash
PYTHONPATH=src .venv/bin/python -B -m synapsys_memory.client_terminal
```

In the approval terminal, run the controller with the socket path and the
private project policy.

```bash
PYTHONPATH=src .venv/bin/python -B -m synapsys_memory.controller_cli \
  --policy /absolute/path/to/projects.json --space pilot --rights read \
  --opencode "$HOME/.opencode/bin/opencode" \
  --interactive "/path/printed/by/client-terminal/client.sock"
```

Approve each project in the controller terminal. OpenCode starts in the
second terminal and its Status panel opens with `Ctrl+X`, then `s`. An
approved switch closes the old sandbox and connects the same second terminal
to a fresh pseudoterminal in the new sandbox. Use `:salir` in the approval
terminal to close the active sandbox, then `Ctrl+C` in the waiting OpenCode
terminal to stop its listener.

The integrated flow passed with two disposable projects and the installed
broker. The native OpenCode TUI displayed `synapsys-memory Connected` in A
and again in B. Both grants were read-only. The broker rejected A's token
and A's namespace was closed before B's grant. Exit revoked B and closed its
namespace. The broker kept the same PID and the SQLite file kept the same
hash. The test used no prompts, agents, or model inference.

### Interactive `memory_get` after a project switch (PASS)

A temporary OpenCode 2.2.5 build from the pinned source above added a test
action to the Status panel. Opening Status sent a local RPC to the OpenCode
server already serving that TUI. The server called `Mcp.Service.callTool`
on its configured SynapSyS-MemorY connection over MCP stdio. The installed
OpenCode binary and controller code stayed intact.

The installed broker executable ran a separate test process with disposable
SQLite containing one synthetic memory per project. A recovered its own
memory and received `null` for B's ID. After owner approval, the controller
revoked A and closed its namespace before granting B read access. From the
same second terminal, B recovered its own memory and received a successful
`null` for the ID verified in A. Both successful reads matched the expected
ID, scope, revision, and body hash. Both interactive sessions exposed only
read tools.

Exit revoked B and closed its sandbox. The test process and disposable
SQLite were removed. The persistent broker kept its PID and database hash.
The test used no prompts, agents, or model inference.

Run the interactive verification from the checkout root on Linux with Git,
Bubblewrap, the repository's `.venv` with the MCP extra, and native OpenCode
2.2.5 with bundled Bun 1.4.4.

```bash
PYTHONPATH=src .venv/bin/python -B scripts/verify_opencode_interactive_memory_get.py
```

Use `--opencode /path/to/opencode` if the executable lives outside
`~/.opencode/bin/opencode`. Preparation downloads the pinned OpenCode source
and locked dependencies with install scripts disabled, applies the
[test patch](../scripts/fixtures/opencode-2.2.5-interactive-memory-get.patch),
and builds a temporary executable. The script drives the approval and
OpenCode terminals through pseudoterminals. It starts its own broker with
private sockets and disposable SQLite. The TUI runs inside Bubblewrap with
network isolation.

A successful run prints a JSON summary with `"result": "PASS"` after four
real MCP calls, grant revocation, sandbox closure, and cleanup. The
temporary executable, project fixtures, and SQLite database are deleted
when the script exits.

### Unmodified OpenCode with a simulated provider (PASS)

The installed OpenCode 2.2.5 executable completed its ordinary interactive
tool workflow using a deterministic local provider plugin. The plugin used
the supported SDK hook and returned scripted tool-call events. OpenCode
executed the calls through its normal tool registry and MCP connection.
The provider verified the tool results and returned a fixed final response
to the TUI.

The test submitted one synthetic prompt in each disposable project. A
recovered its own record and received `null` for B. After the approved switch,
B recovered its record with its read-only grant and received `null` for A.
The old grant and PID namespace closed before B's grant. The script checked
the installed executable's hash, revoked B at exit, and removed the temporary
broker, SQLite database, configuration and workspaces. The provider returned
fixed SDK events with zero token usage.

```bash
PYTHONPATH=src .venv/bin/python -B scripts/verify_opencode_simulated_provider.py
```

This test uses the repository's MCP-enabled `.venv`, native OpenCode 2.2.5,
and Bubblewrap. It prints a JSON `PASS` after checking four MCP calls and
cleanup. Its provider plugin stays inside the disposable project and the
application keeps its isolated network namespace throughout the test.

### Restricted local runtime access

Pass `--runtime-model MODEL_ID` to the controller with `--opencode` to
authorize one model for each approved sandbox. The host runtime must listen
on `127.0.0.1:11434`. Use `--runtime-port PORT` if the local server uses
another port. The managed OpenCode configuration selects
`local-runtime/chat`, which sends the authorized model ID.
[OpenCode provider configuration](https://opencode.ai/docs/providers/)

Bubblewrap keeps its own network namespace. OpenCode reaches a loopback
relay inside that namespace. The relay connects through a private Unix
socket whose host worker forwards only `POST /v1/chat/completions` to the
configured host loopback port. The worker accepts text messages and
function tools with a JSON body of up to 1 MiB. It rejects other models,
other endpoints, image URLs, and redirects. Client headers such as
authorization and cookies are stripped. The controller removes the socket
and closes its connections before activating another project.

The HTTP compatibility test uses a simulated runtime and a disposable
broker. It drives the installed OpenCode through four MCP calls in two
temporary projects. It checks model and endpoint restrictions, streamed
responses, project memory isolation, and closure of a stream that remains
open during the switch.

```bash
PYTHONPATH=src .venv/bin/python -B scripts/verify_opencode_runtime_bridge.py
```

The real runtime test remains pending. For Ollama, the bridge targets its
OpenAI-compatible chat API. Ollama's local-only setting and the installed
model determine where inference runs.
[Ollama chat API](https://docs.ollama.com/api/openai-compatibility)
· [Ollama local-only mode](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features)

### Installed broker permissions and Mobile policy preflight (PASS)

On Kubuntu, the data and admin sockets resolved to the installed broker's
PID and owner UID. The sockets, SQLite file and admin key had mode `0600`.
The runtime, configuration and state directories had mode `0700`. The
broker rejected a grant bound to a different UID when the owner UID
presented it.

A disposable Bubblewrap workspace reached the installed broker with a
read-only grant. It saw the excluded directory as empty and read-only, while
the host fixture stayed intact. Both names of an audited internal hardlink
were visible. The database, admin socket and key were outside the sandbox.
Exit removed the test grants. The broker stayed active and SQLite kept its
hash.

The Mobile directory passed the controller's metadata audit with a
read-only policy that excludes its generated `build` directory. The policy
was checked on the host and kept private. Activation of that workspace
through the owner's approval terminal is still pending.

## Remaining v1 acceptance

**V1 stable is BLOCKED.**

- Exercise the controller with the operator's intended workspaces and verify
  their file permissions from an approved session. Validate separate OS
  identities for any deployment that uses them.
- Verify a tool call from OpenCode through the restricted bridge with the
  intended local model runtime.
