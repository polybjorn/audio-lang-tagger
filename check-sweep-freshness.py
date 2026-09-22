#!/usr/bin/env python3
"""Did the branch sweep actually run?

    API=https://forge/api/v1/repos/owner/repo TOKEN=... ./check-sweep-freshness.py
    ./check-sweep-freshness.py --hours 72

The sweep in .forgejo/workflows/delete-merged-branch.yml is the backstop for a
merge whose delete event never arrived. It has the same blind spot one layer up:
if its timer stops firing, branches pile up in exactly the same silence, because
the absence of a run looks identical to nothing to do. This closes that, from
ci.yml, which runs on every push to main anyway - a watchdog on its own schedule
would need its own watchdog.

Exit codes: 0 the sweep is alive, 1 it is not, 2 the check could not tell.
The third is deliberate. "No sweep found" and "stopped looking" are different
facts and only one of them is a failure of the sweep; reporting a missing sweep
because paging ran out would turn a working backstop into a red job, and a job
that cries wolf gets muted.

Ported from bjorn/rovar-no's scripts/sweep-freshness-core.mjs and
check-sweep-freshness.mjs. Stdlib only, because this repo has no npm and the
runner image is python:3.12.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

LIMIT = 50
MAX_PAGES = 10


def started_at(task):
    """run_started_at is the honest field. created_at is when the task was
    queued, and a task can sit queued behind the single runner slot this fleet
    has - six minutes of it was measured on 2026-09-15. They are usually equal;
    when they are not, the later one is when the work happened."""
    raw = task.get("run_started_at") or task.get("created_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def sweep_tasks(tasks, job="sweep", workflow="delete-merged-branch.yml"):
    return [t for t in tasks if t.get("name") == job and t.get("workflow_id") == workflow]


def collect(get_page, since, limit=LIMIT, max_pages=MAX_PAGES):
    """One page at a time, newest first, stopping as soon as no later page can
    hold anything worth accepting. The listing is ordered by created_at
    descending, so once a page's oldest task predates the window, every task
    after it does too.

    max_pages is a real bound rather than a formality: a busy day on the single
    runner can push a daily sweep off the first page. Running out is reported,
    not swallowed."""
    tasks = []
    for page in range(1, max_pages + 1):
        batch = get_page(page)
        tasks.extend(batch)
        if len(batch) < limit:
            return tasks, True, page
        oldest = started_at(batch[-1])
        if oldest is not None and oldest < since:
            return tasks, True, page
    return tasks, False, max_pages


def verdict(tasks, window_hours=48, now=None, job="sweep", workflow="delete-merged-branch.yml"):
    now = now or datetime.now(timezone.utc)
    since = now.timestamp() - window_hours * 3600
    dated = [(started_at(t), t) for t in sweep_tasks(tasks, job, workflow)]
    dated = [(d, t) for d, t in dated if d is not None]
    dated.sort(key=lambda pair: pair[0], reverse=True)

    succeeded = [(d, t) for d, t in dated if t.get("status") == "success"]
    failed_in_window = [
        t for d, t in dated if t.get("status") == "failure" and d.timestamp() >= since
    ]
    last = succeeded[0] if succeeded else None
    age_hours = (now.timestamp() - last[0].timestamp()) / 3600 if last else None

    return {
        "ok": last is not None and age_hours <= window_hours,
        "last": last[1] if last else None,
        "age_hours": age_hours,
        "window_hours": window_hours,
        "failed_in_window": failed_in_window,
        # A skipped task is not a run. The sweep job carries an `if:` that keeps
        # it off pull_request events, so every merge files a SKIPPED sweep task
        # alongside the real delete one. Counting those as liveness would make
        # this check pass forever on merge traffic alone, which is exactly the
        # hollow reassurance it exists to avoid.
        "ever_ran": bool(dated),
    }


def summary_lines(result):
    lines = []
    if result["ok"]:
        lines.append(
            f"branch sweep last succeeded {result['age_hours']:.1f}h ago, "
            f"inside the {result['window_hours']}h window"
        )
    elif result["last"]:
        lines.append(
            f"branch sweep last succeeded {result['age_hours']:.1f}h ago, "
            f"outside the {result['window_hours']}h window"
        )
    elif result["ever_ran"]:
        lines.append("branch sweep has run but never succeeded, so nothing is sweeping merged branches")
    else:
        lines.append("no branch sweep has ever run")

    for t in result["failed_in_window"]:
        lines.append(f"  FAILED  task {t.get('id')}  {t.get('run_started_at') or t.get('created_at')}")

    if result["ok"]:
        if result["failed_in_window"]:
            lines.append("")
            lines.append("The sweep is alive but not clean. A failing sweep leaves merged branches")
            lines.append("on the remote exactly as a missing one does; read the job log.")
        return lines

    lines.append("")
    lines.append("The sweep is the backstop for a merge whose delete event never arrived.")
    lines.append("While it is not running, nothing is, and the symptom is branches quietly")
    lines.append("accumulating, which is invisible until someone reads the branch list.")
    lines.append("Check the schedule in .forgejo/workflows/delete-merged-branch.yml and run")
    lines.append("it once by hand (workflow_dispatch) to confirm the job itself still works.")
    return lines


def die(*lines):
    for line in lines:
        print(line, file=sys.stderr)
    raise SystemExit(2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=48.0)
    parser.add_argument("--job", default="sweep")
    parser.add_argument("--workflow", default="delete-merged-branch.yml")
    args = parser.parse_args(argv)

    if args.hours <= 0:
        die(f"--hours must be a positive number, got {args.hours}")

    api = os.environ.get("API")
    token = os.environ.get("TOKEN")
    if not api:
        die("API is not set. It is the repo API base, e.g. https://forge/api/v1/repos/owner/repo")
    if not token:
        die(
            "TOKEN is not set. It needs read access to the repo's Actions listing;",
            "the automatic Actions token may not be enough on this forge.",
        )

    def get_page(page):
        url = f"{api}/actions/tasks?limit={LIMIT}&page={page}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"token {token}")
        try:
            with urllib.request.urlopen(req) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            die(f"could not list action tasks: {exc.code} {exc.reason}")
        except urllib.error.URLError as exc:
            die(f"could not reach the forge: {exc.reason}")
        listing = body.get("workflow_runs") if isinstance(body, dict) else None
        if not isinstance(listing, list):
            die(f"the tasks listing did not answer with a list: {json.dumps(body)[:200]}")
        return listing

    now = datetime.now(timezone.utc)
    since = datetime.fromtimestamp(now.timestamp() - args.hours * 3600, timezone.utc)
    tasks, complete, pages = collect(get_page, since)
    if not complete:
        die(
            f"stopped after {pages} pages of action tasks without reaching the {args.hours}h boundary.",
            "Raise MAX_PAGES in this script, or narrow --hours. Reporting a missing sweep",
            "would be a guess.",
        )

    result = verdict(tasks, window_hours=args.hours, now=now, job=args.job, workflow=args.workflow)
    for line in summary_lines(result):
        print(line)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
