"""Demostracion sin IA ni escrituras en proyectos reales."""
from pathlib import Path
import secrets
import tempfile

from synapsys_memory.broker import BrokerDaemon, BrokerClient, admin_request


def main():
    with tempfile.TemporaryDirectory(prefix="symem-switch-") as root:
        root = Path(root)
        key = secrets.token_urlsafe(48)
        admin_socket = root / "admin.sock"
        client_socket = root / "client.sock"
        with BrokerDaemon(root / "memory.sqlite3", client_socket, admin_socket, admin_key=key):
            old = admin_request(admin_socket, key, "grant", session="qwen-demo", client="qwen",
                                space="privado", project="sypminer-terminal-pro", rights="read,write")
            qwen_terminal = BrokerClient(client_socket, old["token"])
            qwen_terminal.remember("Estado de SYPminer solo visible en Terminal PRO", kind="decision")
            new = admin_request(admin_socket, key, "switch", session="qwen-demo", client="qwen",
                                space="privado", project="synapsys", rights="read,write")
            qwen_synapsys = BrokerClient(client_socket, new["token"])
            assert not qwen_synapsys.recall("SYPminer")
            qwen_synapsys.remember("Estado de SynapSyS independiente", kind="decision")
            try:
                qwen_terminal.recall("SYPminer")
            except PermissionError:
                pass
            else:
                raise AssertionError("El token antiguo deberia estar revocado")
            assert qwen_synapsys.recall("SynapSyS")
            print("PASS: Qwen cambia a SynapSyS; SYPminer queda fuera de alcance")


if __name__ == "__main__":
    main()
