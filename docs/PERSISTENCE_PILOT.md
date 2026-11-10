# Real persistent SQLite pilot (no AI)

Status: experimental. Not v1 stable, and not an OpenCode tool-call test.

One trusted, offline command exercises `src/synapsys_memory/broker.py` against
`~/.local/state/synapsys-memory/memory.sqlite3`. If an existing DB is private,
it is reused without replacing it. No user project is granted: only the
synthetic `symem-validation/persistence-pilot` scope is written.

The pilot creates a synthetic memory, revokes writer rights, stops the broker,
starts a fresh broker with a new admin secret, recovers the memory as a
different read-only client, checks FTS5 search, verifies another project
cannot read it, and proves the read-only client cannot write. It revokes the
new grants and stops the second broker. The synthetic marker stays in SQLite
for future explicitly authorized validation; it is not a user memory.

No LLM, Strata, Ollama, AI client, model, network, sudo, installation, or
real workspace access. Tokens remain only in the test process; the evidence
file in `~/.local/state/synapsys-memory/validaciones/` contains no credentials.
The test refuses symlinked/unsafe files, open directories, and a broker socket
in the default location. It does not chmod or replace existing databases.

Run exactly once on the target Kubuntu computer:

```bash
cd "$HOME/Documentos/SynapSyS-MemorY"
PYTHONPATH=src .venv/bin/python scripts/verify_persistent_memory.py
```

A local-only temporary test is available with `--db /path/to/private/test.sqlite3`.
Do not pass a DB path inside Git checkout. Source code and unit tests belong on
GitHub; the SQLite database and its markers belong only to the user's machine.

Remaining for v1 stable: managed persistence lifecycle as a supported service,
real OpenCode memory tool usage (not just MCP list), per-agent OS identities,
operator consent and recovery, and a reviewed model-runtime bridge. Previous
sandbox/connection validations are not repeated by this pilot.
