"""The sweep enumerates nested herd/ branches, and git still behaves that way.

The sweep job in .forgejo/workflows/delete-merged-branch.yml lists candidate
branches with `git for-each-ref`. A single `*` in a for-each-ref pattern does
not match across a slash, so `refs/heads/herd/*` finds `herd/flat` and skips
`herd/nested/deep` - and skips it silently, because a branch that is never
listed never reaches the `keep` line either. The sweep then looks like it had
nothing to do.

That matters because the delete job in the same file guards on
startsWith(head.ref, 'herd/'), which does match nested names: the two halves
would disagree on scope in exactly the case the backstop exists for.

Two tests, and the second is the point. Asserting the workflow uses `**` only
repeats what the file says; seeding a real repo and watching `*` miss the
nested branch is what makes the reason checkable, and what will say so if git
ever changes its mind.
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".forgejo" / "workflows" / "delete-merged-branch.yml"


class SweepPattern(unittest.TestCase):
    def test_the_sweep_enumerates_with_a_double_star(self):
        patterns = re.findall(r"for-each-ref[^\n]*'(refs/heads/[^']*)'", WORKFLOW.read_text())
        self.assertTrue(patterns, "no for-each-ref pattern found in the sweep workflow")
        for pattern in patterns:
            self.assertFalse(
                re.search(r"(?<!\*)\*(?!\*)", pattern),
                f"{pattern} uses a single star, which skips nested branches; use **",
            )


@unittest.skipIf(shutil.which("git") is None, "git is not on PATH")
class GitStillSkipsNestedRefs(unittest.TestCase):
    """The measurement the comment in the workflow rests on."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        run = lambda *args: subprocess.run(
            ["git", "-C", cls.tmp, *args], check=True, capture_output=True, text=True
        )
        subprocess.run(["git", "init", "-q", cls.tmp], check=True, capture_output=True)
        run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "root")
        run("branch", "herd/flat")
        run("branch", "herd/nested/deep")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def listed(self, pattern):
        out = subprocess.run(
            ["git", "-C", self.tmp, "for-each-ref", "--format=%(refname:short)", pattern],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
        return set(out)

    def test_a_single_star_misses_the_nested_branch(self):
        self.assertEqual(self.listed("refs/heads/herd/*"), {"herd/flat"})

    def test_a_double_star_finds_both(self):
        self.assertEqual(self.listed("refs/heads/herd/**"), {"herd/flat", "herd/nested/deep"})
