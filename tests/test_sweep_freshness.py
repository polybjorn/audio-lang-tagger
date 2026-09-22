"""The parts of the sweep-liveness check that touch no network.

The paging and the verdict are where the bugs are, and they are the half that
can be covered without a forge. Stdlib only, run from repo root:

    python3 -m unittest discover -s tests

Half of these assert the check FAILS. A liveness gate that cannot go red is
worse than no gate: it is the same silence with a green tick over it, which is
the fault the sweep itself exists to remove.
"""

import contextlib
import importlib.util
import io
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "check-sweep-freshness.py"

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


def load_module():
    spec = importlib.util.spec_from_file_location("check_sweep_freshness", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def task(hours_ago, status="success", name="sweep", workflow="delete-merged-branch.yml", task_id=1):
    return {
        "id": task_id,
        "name": name,
        "status": status,
        "workflow_id": workflow,
        "run_started_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "created_at": (NOW - timedelta(hours=hours_ago + 1)).isoformat(),
    }


class StartedAt(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_prefers_run_started_over_created(self):
        """A task queued behind the single runner slot started later than it was
        created, and the later one is when the work happened."""
        t = task(2)
        self.assertEqual(self.mod.started_at(t), NOW - timedelta(hours=2))

    def test_falls_back_to_created_at(self):
        t = task(2)
        del t["run_started_at"]
        self.assertEqual(self.mod.started_at(t), NOW - timedelta(hours=3))

    def test_missing_and_unparseable_are_none(self):
        self.assertIsNone(self.mod.started_at({}))
        self.assertIsNone(self.mod.started_at({"run_started_at": "not a date"}))


class SweepTasks(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_filters_on_both_job_and_workflow(self):
        tasks = [
            task(1, name="sweep"),
            task(1, name="delete"),
            task(1, name="sweep", workflow="ci.yml"),
            task(1, name="test", workflow="ci.yml"),
        ]
        self.assertEqual(len(self.mod.sweep_tasks(tasks)), 1)


class Collect(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        self.since = NOW - timedelta(hours=48)

    def test_stops_on_a_short_page(self):
        pages = {1: [task(1)] * 10}
        tasks, complete, seen = self.mod.collect(lambda p: pages.get(p, []), self.since)
        self.assertTrue(complete)
        self.assertEqual(seen, 1)
        self.assertEqual(len(tasks), 10)

    def test_stops_once_a_page_predates_the_window(self):
        pages = {1: [task(1)] * 49 + [task(100)], 2: [task(200)] * 50}
        tasks, complete, seen = self.mod.collect(lambda p: pages.get(p, []), self.since)
        self.assertTrue(complete)
        self.assertEqual(seen, 1)
        self.assertEqual(len(tasks), 50)

    def test_running_out_of_pages_is_reported_not_swallowed(self):
        """The whole point of the third exit code: stopping early must never be
        reported as a missing sweep."""
        tasks, complete, seen = self.mod.collect(
            lambda p: [task(1)] * 50, self.since, max_pages=3
        )
        self.assertFalse(complete)
        self.assertEqual(seen, 3)


class Verdict(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def result(self, tasks, hours=48):
        return self.mod.verdict(tasks, window_hours=hours, now=NOW)

    def test_a_recent_success_is_alive(self):
        r = self.result([task(3)])
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["age_hours"], 3.0, places=3)

    def test_a_success_outside_the_window_is_not(self):
        r = self.result([task(60)])
        self.assertFalse(r["ok"])
        self.assertIsNotNone(r["last"])
        self.assertIn("outside the 48h window", self.mod.summary_lines(r)[0])

    def test_the_newest_success_wins_over_an_older_one(self):
        r = self.result([task(60, task_id=1), task(2, task_id=2)])
        self.assertTrue(r["ok"])
        self.assertEqual(r["last"]["id"], 2)

    def test_skipped_tasks_are_not_liveness(self):
        """Every merge files a skipped sweep task. If those counted, merge
        traffic alone would keep this green while nothing swept anything."""
        r = self.result([task(1, status="skipped"), task(1, status="skipped")])
        self.assertFalse(r["ok"])
        self.assertIsNone(r["last"])
        self.assertTrue(r["ever_ran"])
        self.assertIn("has run but never succeeded", self.mod.summary_lines(r)[0])

    def test_a_failing_sweep_is_not_liveness_either(self):
        r = self.result([task(1, status="failure")])
        self.assertFalse(r["ok"])
        self.assertIsNone(r["last"])

    def test_nothing_at_all_reads_as_never_ran(self):
        r = self.result([])
        self.assertFalse(r["ok"])
        self.assertFalse(r["ever_ran"])
        self.assertEqual(self.mod.summary_lines(r)[0], "no branch sweep has ever run")

    def test_a_failure_inside_the_window_is_reported_alongside_a_success(self):
        r = self.result([task(2, task_id=7), task(5, status="failure", task_id=8)])
        self.assertTrue(r["ok"])
        self.assertEqual([t["id"] for t in r["failed_in_window"]], [8])
        lines = self.mod.summary_lines(r)
        self.assertIn("FAILED  task 8", lines[1])
        self.assertIn("alive but not clean", " ".join(lines))

    def test_an_old_failure_is_not_reported(self):
        r = self.result([task(2, task_id=7), task(60, status="failure", task_id=8)])
        self.assertTrue(r["ok"])
        self.assertEqual(r["failed_in_window"], [])

    def test_the_window_is_honoured(self):
        self.assertFalse(self.result([task(60)], hours=48)["ok"])
        self.assertTrue(self.result([task(60)], hours=72)["ok"])

    def test_other_jobs_do_not_count_as_a_sweep(self):
        """A green `delete` or `test` task says nothing about the sweep."""
        r = self.result([task(1, name="delete"), task(1, name="test", workflow="ci.yml")])
        self.assertFalse(r["ok"])
        self.assertFalse(r["ever_ran"])


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_a_missing_api_exits_two(self):
        with unittest.mock.patch.dict("os.environ", {"TOKEN": "x"}, clear=True):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                self.mod.main([])
        self.assertEqual(caught.exception.code, 2)

    def test_a_missing_token_exits_two(self):
        with unittest.mock.patch.dict("os.environ", {"API": "https://forge/api"}, clear=True):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                self.mod.main([])
        self.assertEqual(caught.exception.code, 2)

    def test_a_nonsense_window_exits_two(self):
        with unittest.mock.patch.dict("os.environ", {"API": "a", "TOKEN": "b"}, clear=True):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                self.mod.main(["--hours", "0"])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
