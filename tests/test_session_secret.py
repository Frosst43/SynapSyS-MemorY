"""Credentials are file-mounted inside sandbox; never command-line arguments."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from synapsys_memory.session_secret import read_token_file, token_from_environment


class SecretTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='symem-token-unit-')
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'session.token'
        self.valid='A'*48
        self.path.write_text(self.valid+'\n')
        self.path.chmod(0o600)

    def test_read_private_session_file(self):
        self.assertEqual(self.valid,read_token_file(self.path))

    def test_env_file_selection(self):
        with patch.dict(os.environ,{'SYMEM_SESSION_TOKEN_FILE':str(self.path), 'SYMEM_SESSION_TOKEN':''}):
            self.assertEqual(self.valid,token_from_environment())

    def test_reject_ambiguous_token_sources(self):
        with patch.dict(os.environ,{'SYMEM_SESSION_TOKEN_FILE':str(self.path), 'SYMEM_SESSION_TOKEN':self.valid}):
            with self.assertRaises(ValueError):token_from_environment()

    def test_reject_world_readable_token(self):
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):read_token_file(self.path)

    def test_reject_symlinked_token(self):
        alias=self.path.parent/'alias.token'
        alias.symlink_to(self.path)
        with self.assertRaises(ValueError):read_token_file(alias)

    def test_reject_invalid_token_string(self):
        self.path.write_text('not-a-valid-token')
        with self.assertRaises(ValueError):read_token_file(self.path)

    def test_reject_missing_env_token(self):
        with patch.dict(os.environ,{'SYMEM_SESSION_TOKEN_FILE':'','SYMEM_SESSION_TOKEN':''}):
            with self.assertRaises(ValueError):token_from_environment()

    def test_inline_legacy_token_still_supported(self):
        with patch.dict(os.environ,{'SYMEM_SESSION_TOKEN_FILE':'','SYMEM_SESSION_TOKEN':self.valid}):
            self.assertEqual(self.valid,token_from_environment())

    def test_reject_too_long_file(self):
        self.path.write_text('A'*1025)
        with self.assertRaises(ValueError):read_token_file(self.path)


if __name__=='__main__':unittest.main()
