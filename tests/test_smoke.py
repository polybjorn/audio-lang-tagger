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


class IgnoreRules(unittest.TestCase):
    """The extras filter has to skip by convention without eating a title
    that merely contains the word."""

    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()
        cls.mod.USE_IGNORE = True
        cls.mod.IGNORE_EXTRA = []

    def test_skips_extras(self):
        for path in ("/m/Film (1999)/Film-trailer.mkv",
                     "/m/Film (1999)/sample.mkv",
                     "/m/Film (1999)/Extras/Making Of.mkv",
                     "/m/Doc (2011)/Behind The Scenes/clip.mkv",
                     "/m/Doc (2011)/Doc-featurette.mkv"):
            self.assertTrue(self.mod.is_ignored(path), path)

    def test_keeps_titles_containing_the_word(self):
        for path in ("/m/Film (1999)/Film.mkv",
                     "/s/Trailer Park Boys/Season 1/Trailer Park Boys - "
                     "S01E01.mkv",
                     "/s/Sample People (2000)/Sample People.mkv"):
            self.assertFalse(self.mod.is_ignored(path), path)

    def test_user_patterns_are_plain_substrings(self):
        self.mod.IGNORE_EXTRA = ["/kids/"]
        try:
            self.assertTrue(self.mod.is_ignored("/m/kids/Film.mkv"))
            self.assertFalse(self.mod.is_ignored("/m/adults/Film.mkv"))
        finally:
            self.mod.IGNORE_EXTRA = []

    def test_no_ignore_disables_everything(self):
        self.mod.USE_IGNORE = False
        try:
            self.assertFalse(self.mod.is_ignored("/m/Film-trailer.mkv"))
        finally:
            self.mod.USE_IGNORE = True


class LedgerReading(unittest.TestCase):
    """--undo drives off ledger_in_force, so a reverted row reappearing there
    would re-revert a track the user already put back."""

    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def rows(self, *specs):
        return [{"path": p, "track": t, "mode": m, "new": c, "old": "und",
                 "when": w} for w, p, t, c, m in specs]

    def test_undone_rows_drop_out(self):
        rows = self.rows(
            ("2026-01-01T00:00:00", "/a.mkv", 1, "eng", "auto"),
            ("2026-01-02T00:00:00", "/b.mkv", 1, "zxx", "bulk"),
            ("2026-01-03T00:00:00", "/b.mkv", 1, "und", "undo"),
        )
        live = self.mod.ledger_in_force(rows)
        self.assertEqual([r["path"] for r in live], ["/a.mkv"])

    def test_retag_after_undo_counts_again(self):
        rows = self.rows(
            ("2026-01-01T00:00:00", "/a.mkv", 1, "eng", "auto"),
            ("2026-01-02T00:00:00", "/a.mkv", 1, "und", "undo"),
            ("2026-01-03T00:00:00", "/a.mkv", 1, "nor", "manual"),
        )
        live = self.mod.ledger_in_force(rows)
        self.assertEqual([(r["path"], r["new"]) for r in live],
                         [("/a.mkv", "nor")])

    def test_tracks_are_independent(self):
        rows = self.rows(
            ("2026-01-01T00:00:00", "/a.mkv", 1, "eng", "auto"),
            ("2026-01-02T00:00:00", "/a.mkv", 2, "fra", "auto"),
            ("2026-01-03T00:00:00", "/a.mkv", 1, "und", "undo"),
        )
        live = self.mod.ledger_in_force(rows)
        self.assertEqual([r["track"] for r in live], [2])

    def test_newest_first(self):
        rows = self.rows(
            ("2026-01-01T00:00:00", "/a.mkv", 1, "eng", "auto"),
            ("2026-01-05T00:00:00", "/b.mkv", 1, "nor", "manual"),
        )
        live = self.mod.ledger_in_force(rows)
        self.assertEqual([r["path"] for r in live], ["/b.mkv", "/a.mkv"])

    def test_malformed_lines_are_skipped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "tags.tsv"
            log.write_text(
                "2026-01-01T00:00:00\t/a.mkv\ta1\tund->eng\tauto\t0.9\t80\n"
                "truncated line\n"
                "2026-01-02T00:00:00\t/b.mkv\tax\tund->nor\tmanual\t-\t-\n"
                "2026-01-03T00:00:00\t/c.mkv\ta1\tnocode\tauto\t-\t-\n")
            self.mod.TAG_LOG_FILE = str(log)
            rows = self.mod.read_ledger()
        self.assertEqual([r["path"] for r in rows], ["/a.mkv"])


if __name__ == "__main__":
    unittest.main()
