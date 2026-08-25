"""Smoke gate: the script imports, builds its parser, and its pure decision
logic behaves. Stdlib only, run from repo root:

    python3 -m unittest discover -s tests
"""

import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "audio-lang-tagger.py"

# Distinguishes "caller passed nothing" from "caller passed None on purpose".
DEFAULT = object()


def load_module():
    """A fresh module per test class, so a class that rebinds a global
    (USE_IGNORE, TAG_LOG_FILE) cannot leak it into another."""
    spec = importlib.util.spec_from_file_location("audio_lang_tagger", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class EntrypointSmoke(unittest.TestCase):
    """--help exercises the imports and the argparse wiring without touching
    any media, so a broken script fails here rather than part-way through an
    unattended run."""

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


class PathScoping(unittest.TestCase):
    """A path that does not exist has to stop the run: reporting zero untagged
    tracks for an unmounted share is the one wrong answer with no symptom."""

    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def test_missing_path_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as caught, \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.mod.find_mkv_files([Path(tmp) / "not-here"])
            self.assertEqual(caught.exception.code, 1)
            self.assertIn("not-here", out.getvalue())

    def test_existing_paths_are_returned_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            mkv = Path(tmp) / "Film.mkv"
            mkv.touch()
            self.assertEqual(self.mod.find_mkv_files([tmp]), [mkv])
            self.assertEqual(self.mod.find_mkv_files([mkv]), [mkv])


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


class CoherenceFloor(unittest.TestCase):
    """The floor separates sparse speech from a music hallucination, which is
    the distinction a single confidence number cannot make."""

    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def test_short_transcript_needs_a_high_unique_ratio(self):
        self.assertFalse(self.mod.repetitive(0.5, 40))
        self.assertTrue(self.mod.repetitive(0.49, 40))

    def test_long_transcript_grades_on_distinct_words(self):
        # 425 words at 10% unique is the sung-through case: 43 distinct.
        self.assertTrue(self.mod.repetitive(0.10, 425))
        # 500 at 20% is 100 distinct, the point where volume replaces ratio.
        self.assertFalse(self.mod.repetitive(0.20, 500))
        self.assertTrue(self.mod.repetitive(0.19, 500))
        # A good ratio does not excuse too small a vocabulary: 300 at 25% is
        # 75 distinct, short of the 100 the long branch asks for.
        self.assertTrue(self.mod.repetitive(0.25, 300))

    def test_sparse_speech_is_not_degenerate(self):
        # 99 clean words at a high ratio: short, not a loop.
        self.assertFalse(self.mod.repetitive(0.8, 99))

    def test_coherent_needs_length_as_well(self):
        self.assertTrue(self.mod.coherent(40, 0.8, 8))
        self.assertFalse(self.mod.coherent(39, 0.8, 8))
        self.assertFalse(self.mod.coherent(40, 0.8, 7))
        self.assertFalse(self.mod.coherent(400, 0.1, 400))


class AutoGate(unittest.TestCase):
    """auto_gate decides what --auto writes without asking, so every gate is
    pinned at the value the README and the docstring publish."""

    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def analysis(self, **over):
        a = {"iso1": "en", "prob": 0.95, "detections": [("en", 0.95),
                                                        ("en", 0.93)],
             "chars": 900, "words": 180, "unique": 0.66, "full": False}
        a.update(over)
        return a

    def gate(self, analysis=DEFAULT, exp=("eng", "Sonarr", 2019, set()),
             tracks=((1, {}),), header_langs=None, stream=None):
        return self.mod.auto_gate(
            self.analysis() if analysis is DEFAULT else analysis, exp,
            list(tracks), header_langs or {},
            stream if stream is not None else {})

    def test_clean_track_passes(self):
        self.assertIsNone(self.gate())

    def test_unknown_language_never_tags(self):
        self.assertEqual(self.gate(self.analysis(iso1="xx")), "no detection")
        self.assertEqual(self.gate(None), "no detection")

    def test_no_arr_metadata_never_tags(self):
        self.assertEqual(self.gate(exp=None), "no arr metadata")

    def test_confusable_cluster_never_tags(self):
        for iso1 in ("no", "nn", "da", "sv"):
            self.assertEqual(
                self.gate(self.analysis(iso1=iso1),
                          exp=(self.mod.ISO1_TO_ISO2B[iso1], "Sonarr", 2019,
                               set())),
                "ambiguous language cluster", iso1)

    def test_sampled_scan_needs_two_unanimous_windows(self):
        for dets in ([("en", 0.95)],
                     [("en", 0.95), ("fr", 0.95)],
                     [("en", 0.95), ("en", 0.89)]):
            self.assertEqual(self.gate(self.analysis(detections=dets)),
                             "samples not unanimous", dets)

    def test_whole_track_pass_grades_the_better_reading(self):
        opening_only = self.analysis(full=True, prob=0.42,
                                     detections=[("en", 0.42)])
        self.assertEqual(self.gate(opening_only), "low confidence")
        # The speech window agreeing on the language lifts the same scan over.
        rescued = dict(opening_only, win_iso1="en", win_prob=0.93)
        self.assertIsNone(self.gate(rescued))
        # A window naming a different language is a disagreement, not a lift.
        disagreeing = dict(opening_only, win_iso1="fr", win_prob=0.99)
        self.assertEqual(self.gate(disagreeing), "low confidence")

    def test_degenerate_transcript_never_tags(self):
        self.assertEqual(
            self.gate(self.analysis(chars=900, words=425, unique=0.10)),
            "no coherent transcript")

    def test_disagreeing_arr_never_tags(self):
        self.assertEqual(self.gate(exp=("fre", "Radarr", 2019, set())),
                         "differs from arr language")

    def test_year_gate(self):
        self.assertEqual(self.gate(exp=("eng", "Sonarr", 1939, set())),
                         "year 1939 < 1940")
        self.assertEqual(self.gate(exp=("eng", "Sonarr", None, set())),
                         "year unknown < 1940")
        self.assertIsNone(self.gate(exp=("eng", "Sonarr", 1940, set())))

    def test_music_genre_never_tags(self):
        self.assertEqual(
            self.gate(exp=("eng", "Radarr", 2019, {"drama", "music"})),
            "music/concert genre")

    def test_multiple_audio_tracks_never_tag(self):
        self.assertEqual(self.gate(tracks=((1, {}), (2, {}))),
                         "multiple audio tracks")
        self.assertEqual(self.gate(header_langs={1: "eng", 2: "fre"}),
                         "multiple audio tracks")

    def test_commentary_track_never_tags(self):
        self.assertEqual(
            self.gate(stream={"tags": {"title": "Director Commentary"}}),
            "commentary track")


if __name__ == "__main__":
    unittest.main()
