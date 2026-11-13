"""An arr path reached through a symlink still matches the swept file
(media-tools#55): the hypervisor's arrs store /mnt/storage/..., a symlink to
the /storage/... tree the sweep walks."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from tests.test_smoke import load_module


class SymlinkedArrRoot(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # realpath first: macOS temp dirs sit behind /var -> /private/var.
        base = Path(os.path.realpath(tmp.name))
        show = base / "storage/media/series/Show"
        show.mkdir(parents=True)
        self.file = show / "Show - S01E01.mkv"
        self.file.touch()
        (base / "mnt").mkdir()
        (base / "mnt/storage").symlink_to(base / "storage")
        self.arr_show = str(base / "mnt/storage/media/series/Show")

    def test_expected_language_crosses_the_symlink(self):
        series = [{"path": self.arr_show, "originalLanguage": {"name": "Japanese"},
                   "year": 2001, "genres": [], "id": 7}]

        def run(cmd, **kwargs):
            body = series if cmd[-1].endswith("/series") else []
            return type("Result", (), {"stdout": json.dumps(body)})()

        stubs = {"USE_ARR": True, "arr_endpoint": lambda *a: ("http://arr", "k")}
        for name, value in stubs.items():
            saved = getattr(self.mod, name)
            setattr(self.mod, name, value)
            self.addCleanup(setattr, self.mod, name, saved)
        saved_run = self.mod.subprocess.run
        self.mod.subprocess.run = run
        self.addCleanup(setattr, self.mod.subprocess, "run", saved_run)

        expected = self.mod.build_expected_map()
        self.assertEqual(self.mod.expected_for(self.file, expected)[0], "jpn")


if __name__ == "__main__":
    unittest.main()
