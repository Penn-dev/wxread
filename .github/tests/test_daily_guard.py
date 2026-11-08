import importlib.util
from pathlib import Path
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError, URLError


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("daily_guard", ROOT / ".github/scripts/daily_guard.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)
NOW = datetime(2026, 11, 8, 19, 0, tzinfo=timezone.utc)  # November 9 in Beijing
START = datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc)


def run(run_id=1, **kwargs):
    return {"id": run_id, "path": guard.WORKFLOW, "head_branch": "main",
            "created_at": "2026-11-08T17:40:00Z", "updated_at": "2026-11-08T18:50:00Z", **kwargs}


def jobs(conclusion="success", completed="2026-11-08T18:50:00Z", **kwargs):
    return [{"name": "deploy", "steps": [{"name": guard.READ_STEP,
             "conclusion": conclusion, "completed_at": completed, **kwargs}]}]


class DailyGuardTests(unittest.TestCase):
    def decision(self, runs, job_data=None):
        calls = []
        def fetch(url):
            calls.append(url)
            return {"workflow_runs": runs} if "/jobs?" not in url else {"jobs": job_data or []}
        result = guard.should_run("Penn-dev/wxread", "main", "99", NOW, fetch)
        return result[0], calls

    def test_missing_reading_runs(self):
        self.assertTrue(self.decision([])[0])

    def test_successful_reading_skips(self):
        self.assertFalse(self.decision([run()], jobs())[0])

    def test_skipped_green_workflow_is_not_reading(self):
        self.assertTrue(self.decision([run(conclusion="success")], jobs("skipped"))[0])

    def test_failed_reading_retries(self):
        self.assertTrue(self.decision([run()], jobs("failure"))[0])

    def test_keepalive_failure_does_not_repeat_reading(self):
        self.assertFalse(self.decision([run(conclusion="failure")], jobs())[0])

    def test_yesterday_success_does_not_skip_today(self):
        self.assertTrue(self.decision([run()], jobs(completed="2026-11-08T15:59:59Z"))[0])

    def test_beijing_midnight_counts_today(self):
        self.assertFalse(self.decision([run()], jobs(completed="2026-11-08T16:00:00Z"))[0])

    def test_cross_midnight_delayed_run_counts(self):
        self.assertFalse(self.decision([run(created_at="2026-11-07T17:37:00Z")], jobs())[0])

    def test_old_manual_rerun_counts_by_completion(self):
        self.assertFalse(self.decision([run(created_at="2026-10-01T17:37:00Z")], jobs())[0])

    def test_current_run_and_other_branch_workflow_are_ignored(self):
        result, calls = self.decision([run(99), run(2, head_branch="test"),
                                      run(3, path=".github/workflows/other.yml")], jobs())
        self.assertTrue(result)
        self.assertEqual(len(calls), 1)

    def test_api_failure_fails_closed(self):
        def fail(_):
            raise URLError("offline")
        with self.assertRaises(URLError):
            guard.should_run("Penn-dev/wxread", "main", "99", NOW, fail)

    def test_missing_completion_is_not_success(self):
        self.assertTrue(self.decision([run()], jobs(completed=None))[0])

    def test_other_job_does_not_mark_reading_success(self):
        self.assertFalse(guard.reading_completed_today([{"name": "tests", "steps": jobs()[0]["steps"]}], START))

    def test_incomplete_history_fails_closed(self):
        with self.assertRaises(RuntimeError):
            self.decision([run(i) for i in range(100)])

    def test_transient_api_failures_have_bounded_retries(self):
        with patch.object(guard, "urlopen", side_effect=URLError("offline")) as request:
            with patch.object(guard.time, "sleep"):
                with self.assertRaises(URLError):
                    guard.get_json("https://api.github.com/repos/Penn-dev/wxread/actions/runs")
        self.assertEqual(request.call_count, 3)

    def test_permanent_api_failure_is_not_retried(self):
        error = HTTPError("https://api.github.com", 404, "Not Found", {}, None)
        with patch.object(guard, "urlopen", side_effect=error) as request:
            with self.assertRaises(HTTPError):
                guard.get_json("https://api.github.com/repos/Penn-dev/wxread/actions/runs")
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
