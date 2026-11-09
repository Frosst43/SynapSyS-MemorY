"""Token desde fichero privado, sin volcarlos a logs o argumentos."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from synapsys_memory.session_secret import read_token_file, token_from_environment


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="symem-token-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "session.token"
        self.valid = "A"*48
        self.path.write_text(self.valid+"\n")
        self.path.chmod(0o600)

    def test_valid_private_file(self):
        self.assertEqual(self.valid, read_token_file(self.path))

    def test_token_selected_from_file_env(self):
        with patch.dict(os.environ, {"SYMEM_SESSION_TOKEN_FILE":str(self.path),
                                    "SYMEM_SESSION_TOKEN":""}):
            self.assertEqual(self.valid, token_from_environment())

    def test_reject_ambiguous_sources(self):
        with patch.dict(os.environ, {"SYMEM_SESSION_TOKEN_FILE":str(self.path),
                                    "SYMEM_SESSION_TOKEN":self.valid}):
            with self.assertRaises(ValueError): token_from_environment()

    def test_reject_world_readable(self):
        self.path.chmod(0o644)
        with self.assertRaises(ValueError): read_token_file(self.path)

    def test_reject_symlink(self):
        link = self.path.parent/"link.token"
        link.symlink_to(self.path)
        with self.assertRaises(ValueError): read_token_file(link)

    def test_reject_short_token(self):
        self.path.write_text("not-a-token")
        with self.assertRaises(ValueError): read_token_file(self.path)

    def test_reject_empty_env(self):
        with patch.dict(os.environ, {"SYMEM_SESSION_TOKEN_FILE":"","SYMEM_SESSION_TOKEN":""}):
            with self.assertRaises(ValueError): token_from_environment()

    def test_inline_legacy_still_supported(self):
        with patch.dict(os.environ, {"SYMEM_SESSION_TOKEN_FILE":"","SYMEM_SESSION_TOKEN":self.valid}):
            self.assertEqual(self.valid, token_from_environment())

    def test_oversized_file_rejected(self):
        self.path.write_text("A"*1025)
        with self.assertRaises(ValueError): read_token_file(self.path)


if __name__=="__main__":
    unittest.main()
