import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from osmonitor.config import load_env


class ConfigTests(unittest.TestCase):
    def test_loads_env_file(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            p = Path(d)/'.env'
            p.write_text('GEMINI_API_KEY="test-only"\nGEMINI_MODEL=gemini-3.5-flash-lite\n')
            self.assertTrue(load_env(p))
            self.assertEqual(os.environ['GEMINI_API_KEY'], 'test-only')
            self.assertEqual(os.environ['GEMINI_MODEL'], 'gemini-3.5-flash-lite')

    def test_exported_environment_wins(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'GEMINI_API_KEY':'exported-test'}, clear=True):
            p = Path(d)/'.env'
            p.write_text('GEMINI_API_KEY=file-test\n')
            load_env(p)
            self.assertEqual(os.environ['GEMINI_API_KEY'], 'exported-test')

    def test_missing_file_is_optional(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            self.assertFalse(load_env(Path(d)/'missing.env'))
            self.assertNotIn('GEMINI_API_KEY', os.environ)
