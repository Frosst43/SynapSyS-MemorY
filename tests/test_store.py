"""Pruebas independientes: solo unittest y SQLite de Python."""

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from synapsys_memory.store import MemoryStore
from synapsys_memory.cli import main


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "memory.sqlite3"
        self.db = MemoryStore(self.path, space="shared", project="synapsys")
        self.addCleanup(self.db.close)

    def test_remember_search_get_forget(self):
        saved = self.db.remember("La red de pruebas usa bloques de cuatro minutos.",
                                 title="Consenso SYPC", kind="decision", source="prueba")
        self.assertTrue(saved["created"])
        uid = saved["memory"]["id"]
        found = self.db.recall("bloques consenso")
        self.assertEqual(uid, found[0]["id"])
        self.assertEqual("decision", self.db.get(uid)["kind"])
        self.assertTrue(self.db.forget(uid))
        self.assertIsNone(self.db.get(uid))
        self.assertEqual([], self.db.recall("consenso"))
        self.assertFalse(self.db.forget(uid))

    def test_duplicate_idempotence(self):
        first = self.db.remember("Dato repetido", title="Duplicado")
        second = self.db.remember("Dato repetido", title="Duplicado")
        self.assertFalse(second["created"])
        self.assertEqual(first["memory"]["id"], second["memory"]["id"])
        self.assertEqual(1, self.db.stats()["count"])

    def test_scope_isolation(self):
        a = self.db.remember("Secreto aislado de SynapSyS", title="A")
        with MemoryStore(self.path, space="openclaw", project="synapsys") as other:
            self.assertEqual([], other.recall("Secreto"))
            self.assertIsNone(other.get(a["memory"]["id"]))
            self.assertFalse(other.forget(a["memory"]["id"]))
        with MemoryStore(self.path, space="shared", project="otro") as other:
            self.assertEqual([], other.recall("Secreto"))
        self.assertEqual(1, self.db.stats()["count"])

    def test_shared_scope(self):
        saved = self.db.remember("Recuerdo accesible desde dos procesos", title="Compartido")
        with MemoryStore(self.path, space="shared", project="synapsys") as second:
            self.assertEqual(saved["memory"]["id"], second.recall("accesible")[0]["id"])

    def test_unicode_and_mcp_query_injection(self):
        self.db.remember("Configuracion de contraseñas en español", title="Tildes")
        self.assertEqual(1, len(self.db.recall("CONTRASEÑAS")))
        self.assertEqual(1, len(self.db.recall("contrasenas")))
        self.assertEqual([], self.db.recall('" OR DELETE FROM memories --'))
        self.assertEqual(1, self.db.stats()["count"])

    def test_ranking_title_relevance(self):
        self.db.remember("Palabra suelta: caché", title="Diversos")
        target = self.db.remember("Estrategia caché de resultados", title="caché")
        self.assertEqual(target["memory"]["id"], self.db.recall("caché")[0]["id"])

    def test_budget_and_limits(self):
        self.db.remember("memoria " + "a" * 7000, title="Grande")
        hits = self.db.recall("memoria", max_chars=300)
        self.assertEqual(300, len(hits[0]["body"]))
        self.assertTrue(hits[0]["truncated"])
        with self.assertRaises(ValueError):
            self.db.recall("memoria", limit=50)
        with self.assertRaises(ValueError):
            self.db.recall("memoria", max_chars=2)

    def test_update_reindexes(self):
        uid = self.db.remember("La planta es roja", title="Color")["memory"]["id"]
        updated = self.db.update(uid, body="La planta es azul", title="Color")
        self.assertEqual(uid, updated["id"])
        self.assertEqual([], self.db.recall("roja"))
        self.assertEqual(uid, self.db.recall("azul")[0]["id"])

    def test_update_duplicate_rejected(self):
        uid = self.db.remember("Primera", title="A")["memory"]["id"]
        self.db.remember("Segunda", title="B")
        with self.assertRaises(ValueError):
            self.db.update(uid, body="Segunda", title="B")
        self.assertEqual("Primera", self.db.get(uid)["body"])

    def test_ttl_expiration(self):
        with patch("synapsys_memory.store._now_ms", return_value=1_000_000):
            uid = self.db.remember("Temporal", ttl_days=1)["memory"]["id"]
        with patch("synapsys_memory.store._now_ms", return_value=1_000_000 + 2 * 86_400_000):
            self.assertIsNone(self.db.get(uid))
            self.assertEqual([], self.db.recall("Temporal"))
            self.assertEqual(0, self.db.stats()["count"])

    def test_expired_duplicate_can_be_replaced(self):
        with patch("synapsys_memory.store._now_ms", return_value=1_000_000):
            older = self.db.remember("Vuelve a guardarse", ttl_days=1)["memory"]["id"]
        with patch("synapsys_memory.store._now_ms", return_value=1_000_000 + 2 * 86_400_000):
            newer = self.db.remember("Vuelve a guardarse")
            self.assertTrue(newer["created"])
            self.assertNotEqual(older, newer["memory"]["id"])
            self.assertEqual(1, self.db.stats()["count"])

    def test_competing_identical_writes_deduplicated(self):
        errors = []
        ids = []
        def writer():
            try:
                with MemoryStore(self.path, space="shared", project="synapsys") as s:
                    ids.append(s.remember("Mismo recuerdo concurrente")["memory"]["id"])
            except Exception as err:
                errors.append(err)
        threads = [threading.Thread(target=writer) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        self.assertEqual(1, len(set(ids)))
        self.assertEqual(1, self.db.stats()["count"])

    def test_recent_and_kind(self):
        self.db.remember("A", kind="fact")
        self.db.remember("B", kind="decision")
        self.assertEqual(1, len(self.db.list_recent(kind="decision")))
        self.assertEqual(2, len(self.db.list_recent()))
        with self.assertRaises(ValueError):
            self.db.list_recent(kind="bad")

    def test_backup_is_consistent(self):
        self.db.remember("Datos seguros en respaldo")
        copy = Path(self.tmp.name) / "copy.sqlite3"
        self.db.backup(copy)
        with MemoryStore(copy, space="shared", project="synapsys") as restored:
            self.assertEqual(1, restored.stats()["count"])
            self.assertEqual(1, len(restored.recall("respaldo")))
        with self.assertRaises(ValueError):
            self.db.backup(self.path)
        with self.assertRaises(ValueError):
            self.db.backup(copy)

    def test_validation(self):
        for bad in ("../other", "", "with space"):
            with self.assertRaises(ValueError):
                MemoryStore(self.path, space=bad)
        with self.assertRaises(ValueError):
            self.db.remember("")
        with self.assertRaises(ValueError):
            self.db.remember("texto", kind="invalido")
        with self.assertRaises(ValueError):
            self.db.remember("texto", importance=6)
        with self.assertRaises(ValueError):
            self.db.remember("texto", ttl_days=-2)
        with self.assertRaises(ValueError):
            self.db.remember("texto", tags=["a"] * 13)

    def test_concurrent_writes(self):
        errors = []
        def writer(i):
            try:
                with MemoryStore(self.path, space="shared", project="synapsys") as s:
                    for j in range(10):
                        s.remember(f"Recordatorio unico {i} {j}", title=f"{i}-{j}")
            except Exception as err:
                errors.append(err)
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([], errors)
        self.assertEqual(40, self.db.stats()["count"])

    def test_cli_smoke(self):
        from contextlib import redirect_stdout
        from io import StringIO
        result = StringIO()
        with redirect_stdout(result):
            self.assertEqual(0, main(["--db", str(self.path), "--space", "shared", "--project", "synapsys",
                                     "remember", "Dato desde CLI", "--title", "Terminal"]))
        self.assertTrue(json.loads(result.getvalue())["created"])


if __name__ == "__main__":
    unittest.main()
