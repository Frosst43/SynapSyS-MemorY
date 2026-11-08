"""Pruebas universales: el aislamiento no depende de nombres de proyectos concretos."""

from pathlib import Path
import secrets
import socket
import tempfile
import unittest

from synapsys_memory.broker import BrokerDaemon, BrokerClient, admin_request, request


@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Requiere sockets Unix")
class ProjectAgnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="symem-project-general-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.sock = root / "memory.sock"
        self.admin_sock = root / "admin.sock"
        self.secret = secrets.token_urlsafe(48)
        self.broker = BrokerDaemon(root / "memory.sqlite3", self.sock, self.admin_sock,
                                   admin_key=self.secret)
        self.broker.start()
        self.addCleanup(self.broker.stop)

    def grant(self, session, client, project, rights="read,write"):
        return admin_request(self.admin_sock, self.secret, "grant",
                             session=session, client=client, space="personal",
                             project=project, rights=rights)

    def test_arbitrary_new_project_names_have_same_isolation(self):
        """Una sesion no adquiere derechos sobre otros programas, nuevos o antiguos."""
        projects = ("gestor-domestico", "pixel-photo-route", "sitio-web",
                    "aplicacion-android", "proyecto-futuro-2040")
        clients = []
        for i, project in enumerate(projects):
            authorization = self.grant(f"session-{i}", "opencode", project)
            client = BrokerClient(self.sock, authorization["token"])
            item = client.remember(f"Memoria unica PROYECTO{100 + i}", title=project)
            self.assertEqual(project, item["memory"]["project"])
            clients.append((client, item["memory"]["id"]))

        for i, (client, uid) in enumerate(clients):
            self.assertEqual(1, client.stats()["count"])
            self.assertEqual(uid, client.recall(f"PROYECTO{100 + i}")[0]["id"])
            for j, (_, foreign_uid) in enumerate(clients):
                if i == j:
                    continue
                self.assertIsNone(client.get(foreign_uid))
                self.assertEqual([], client.recall(f"PROYECTO{100 + j}"))

    def test_generic_project_switch_revokes_prior_permissions(self):
        """Cambiar entre proyectos arbitrarios revoca el token anterior."""
        first = self.grant("current-session", "qwen", "proyecto-nuevo-a")
        old = BrokerClient(self.sock, first["token"])
        old_uid = old.remember("dato exclusivo del proyecto A")["memory"]["id"]

        second = admin_request(self.admin_sock, self.secret, "switch",
                               session="current-session", client="qwen",
                               space="personal", project="proyecto-nuevo-b",
                               rights="read,write")
        current = BrokerClient(self.sock, second["token"])
        with self.assertRaises(PermissionError):
            old.recall("exclusivo")
        with self.assertRaises(PermissionError):
            old.remember("intento tardio")
        self.assertIsNone(current.get(old_uid))
        self.assertEqual([], current.recall("exclusivo"))
        self.assertEqual("proyecto-nuevo-b", current.profile()["project"])
        current.remember("dato independiente del proyecto B")

        with self.assertRaises(PermissionError):
            request(self.sock, {"token": second["token"], "action": "recall",
                                "args": {"query": "dato", "project": "proyecto-nuevo-a"}})

    def test_distinct_clients_can_share_explicit_same_project(self):
        """Compartir proyecto no implica autorizar el resto de programas."""
        writer = BrokerClient(self.sock, self.grant("writer", "opencode", "herramienta-nueva")["token"])
        reader = BrokerClient(self.sock, self.grant("reader", "codex", "herramienta-nueva", "read")["token"])
        outside = BrokerClient(self.sock, self.grant("outside", "openclaw", "otro-proyecto", "read")["token"])
        uid = writer.remember("configuracion compartida entre agentes")["memory"]["id"]
        self.assertEqual(uid, reader.recall("compartida")[0]["id"])
        self.assertEqual([], outside.recall("compartida"))
        with self.assertRaises(PermissionError):
            reader.remember("no puede escribir")


if __name__ == "__main__":
    unittest.main()
