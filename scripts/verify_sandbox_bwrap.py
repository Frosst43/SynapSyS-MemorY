"""Real bwrap integration test; creates only temporary projects, no user repos.

Requires /usr/bin/bwrap and kernel user/mount namespaces. No models or MCP.
"""

from pathlib import Path
import json
import tempfile

from synapsys_memory.sandbox import SandboxError, run


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="symem-bwrap-check-") as temp:
        root = Path(temp)
        project_a = root / "demo-app-a"
        project_b = root / "demo-app-b"
        project_a.mkdir()
        project_b.mkdir()
        forbidden = project_b / "secret-from-other-project.txt"
        forbidden.write_text("DO-NOT-EXPOSE", encoding="utf-8")
        policy = root / "policy.json"
        policy.write_text(json.dumps({
            "schema_version": 1,
            "projects": {"demo-app-a": {"workspace": str(project_a), "read_only": False}},
        }), encoding="utf-8")
        policy.chmod(0o600)

        script = (
            "from pathlib import Path; "
            "allowed=Path('/workspace/created-by-sandbox.txt'); "
            "allowed.write_text('ALLOWED',encoding='utf-8'); "
            "other=Path(" + repr(str(forbidden)) + "); "
            "assert not other.exists(), 'Se ve fichero de otro proyecto'; "
            "print('SANDBOX_CHILD_PASS')"
        )
        try:
            result = run(policy, "demo-app-a", ["python3", "-c", script])
        except SandboxError as exc:
            print(f"NO VALIDADO: sandbox indisponible: {exc}")
            return 2
        if result != 0:
            print(f"FAIL: bwrap devolvio codigo {result}; no hay evidencia de aislamiento")
            return 1
        if (project_a / "created-by-sandbox.txt").read_text(encoding="utf-8") != "ALLOWED":
            print("FAIL: escritura en workspace no confirmada")
            return 1
        if forbidden.read_text(encoding="utf-8") != "DO-NOT-EXPOSE":
            print("FAIL: proyecto no autorizado alterado")
            return 1
        print("PASS: proyecto A escribible, proyecto B invisible, fichero B intacto")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
