"""Fail closed unless public Actions history confirms today's reading is missing."""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BEIJING = timezone(timedelta(hours=8))
WORKFLOW = ".github/workflows/deploy.yml"
READ_STEP = "Run deployment script"


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_json(url):
    # This repository is public. No credential or additional token permission is needed.
    request = Request(url, headers={"Accept": "application/vnd.github+json",
                                    "User-Agent": "wxread-daily-guard"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in (403, 429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def reading_completed_today(jobs, day_start):
    # A skipped fallback run also has a green conclusion; only the real reading
    # step is a completion marker. A failed keepalive/post-step must not redo it.
    for job in jobs:
        if job.get("name") != "deploy":
            continue
        for step in job.get("steps", []):
            completed = step.get("completed_at")
            if (step.get("name") == READ_STEP
                    and step.get("conclusion") == "success"
                    and completed and timestamp(completed) >= day_start):
                return True
    return False


def should_run(repository, branch, current_run_id, now, fetch=get_json):
    day_start = now.astimezone(BEIJING).replace(hour=0, minute=0, second=0, microsecond=0)
    base = f"https://api.github.com/repos/{repository}/actions"
    # Include yesterday's creations: a delayed run may finish after midnight.
    # Also inspect the latest 100 runs, including old runs manually re-run today.
    query = urlencode({"branch": branch, "per_page": 100})
    runs = fetch(f"{base}/runs?{query}")["workflow_runs"]
    if (len(runs) == 100
            and all(timestamp(run["created_at"]) >= day_start - timedelta(days=1)
                    for run in runs)):
        raise RuntimeError("Too many recent runs to establish daily completion safely")
    for run in runs:
        if (str(run["id"]) == str(current_run_id)
                or run.get("path", "").split("@", 1)[0] != WORKFLOW
                or run.get("head_branch") != branch
                or timestamp(run["updated_at"]) < day_start):
            continue
        jobs = fetch(f"{base}/runs/{run['id']}/jobs?filter=latest&per_page=100")["jobs"]
        if reading_completed_today(jobs, day_start):
            return False, f"Reading already completed today in run {run['id']}"
    return True, "No completed reading found for today (Asia/Shanghai)"


def main():
    run, reason = should_run(
        os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_REF_NAME"],
        os.environ["GITHUB_RUN_ID"], datetime.now(timezone.utc))
    print(reason)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"should_run={str(run).lower()}\n")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            output.write(f"Daily guard: {reason}\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"::error::Daily guard failed; reading was NOT started: {exc}", file=sys.stderr)
        sys.exit(1)
