"""Smoke gate: the script imports, builds its parser, and the pure formatters
behave. Stdlib only, run from repo root:

    python3 -m unittest discover -s tests
"""

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "audio-lang-tagger.py"


def load_module():
    spec = importlib.util.spec_from_file_location("audio_lang_tagger", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class EntrypointSmoke(unittest.TestCase):
    """--help exercises the imports and the argparse wiring without touching
    any media - the failure class that would otherwise surface as a broken
    unattended run after the next version-pin bump."""

    def test_help_exits_clean(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                              capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())


class Formatters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def test_plural(self):
        self.assertEqual(self.mod.plural(1, "file"), "1 file")
        self.assertEqual(self.mod.plural(2, "file"), "2 files")
        self.assertEqual(self.mod.plural(2, "match", "es"), "2 matches")

    def test_fmt_duration(self):
        self.assertEqual(self.mod.fmt_duration(0), "?")
        self.assertEqual(self.mod.fmt_duration(75), "1m15s")
        self.assertEqual(self.mod.fmt_duration(3900), "1h05m")

    def test_fmt_size(self):
        self.assertEqual(self.mod.fmt_size(0), "?")
        self.assertEqual(self.mod.fmt_size(500 * 1024 ** 2), "500 MB")
        self.assertEqual(self.mod.fmt_size(2 * 1024 ** 3), "2.0 GB")


if __name__ == "__main__":
    unittest.main()
