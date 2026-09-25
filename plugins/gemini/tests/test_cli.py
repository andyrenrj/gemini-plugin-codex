"""CLI job lifecycle regression tests without starting Gemini or real workers."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
    SPEC = importlib.util.spec_from_file_location("gemini_plugin_cli", SCRIPTS / "gemini.py")
    cli = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(cli)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gemini-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "project"
        self.repo.mkdir()
        self.jobs = self.root / "jobs"
        self.jobs.mkdir()
        self.env = patch.dict(os.environ, {"GEMINI_PLUGIN_STATE": str(self.jobs)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.workspaces = patch.object(cli, "workspace", side_effect=lambda path: Path(path).resolve())
        self.workspaces.start()
        self.addCleanup(self.workspaces.stop)

    def invoke(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["gemini.py", *arguments]), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def start(self, process=None, error=None):
        request = {"repo": str(self.repo), "scope": "uncommitted", "snapshot_sha256": "snapshot",
                   "units": [{"label": "a.py", "text": "a.py:1 change"}], "omitted": []}
        exists = Path.exists
        with patch.object(cli, "capture_review", return_value=request), \
                patch.object(cli.shutil, "which", return_value="/fake/agy"), \
                patch.object(cli.sys, "platform", "darwin"), \
                patch.object(Path, "exists", lambda path: str(path) == "/usr/bin/sandbox-exec" or exists(path)), \
                patch.object(cli.subprocess, "Popen", return_value=process, side_effect=error):
            return self.invoke("review", "--repo", str(self.repo), "--background")

    def job(self, name, repo=None, **fields):
        directory = self.jobs / name
        directory.mkdir()
        state = {"id": name, "repo": str(repo or self.repo), "status": "running",
                 "created_at": "2026-09-24T00:00:00Z", **fields}
        (directory / "job.json").write_text(json.dumps(state))
        return directory

    def test_failed_worker_launch_leaves_an_inspectable_failed_job(self):
        code, _, error = self.start(error=OSError("worker launch denied"))
        self.assertEqual(code, 1)
        self.assertIn("worker launch denied", error)
        code, output, error = self.invoke("status", "--repo", str(self.repo))
        self.assertEqual(code, 0, error)
        status = json.loads(output)
        self.assertEqual(status["status"], "failed")
        self.assertIn("Worker launch failed", status["error"])
        self.assertTrue((Path(status["artifacts"]) / "request.json").exists())

    def test_worker_exit_before_first_state_update_is_reported_as_interrupted(self):
        code, output, error = self.start(process=SimpleNamespace(pid=43199))
        self.assertEqual(code, 0, error)
        started = json.loads(output)
        with patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            code, output, error = self.invoke("status", started["job_id"], "--repo", str(self.repo))
        self.assertEqual(code, 0, error)
        status = json.loads(output)
        self.assertEqual(status["status"], "interrupted")
        self.assertIn("no longer running", status["error"])

    def test_process_inspection_denial_does_not_mark_worker_dead(self):
        self.job("active", pid=43200)
        denials = [PermissionError("Operation not permitted"),
                   subprocess.CompletedProcess([], 1, "", "ps: Operation not permitted")]
        for denial in denials:
            with self.subTest(denial=denial):
                options = {"side_effect": denial} if isinstance(denial, Exception) else {"return_value": denial}
                with patch.object(cli.subprocess, "run", **options):
                    code, output, error = self.invoke("status", "active", "--repo", str(self.repo))
                self.assertEqual(code, 0, error)
                status = json.loads(output)
                self.assertEqual(status["status"], "running")
                self.assertIn("unavailable", status["worker_check"])
                self.assertNotIn("error", status)

    def test_selecting_job_does_not_inspect_other_jobs_or_workspaces(self):
        target = self.job("target", pid=43201)
        for name, repo in (("same-workspace", self.repo), ("other-workspace", self.root / "elsewhere")):
            unrelated = self.job(name, repo=repo)
            # Broken launch metadata is irrelevant to the requested job and must
            # not prevent its status from being displayed.
            (unrelated / "launch.json").write_text("not valid json")
        command = subprocess.CompletedProcess([], 0, f"python gemini.py _run {target}\n", "")
        with patch.object(cli.subprocess, "run", return_value=command) as check:
            code, output, error = self.invoke("status", "target", "--repo", str(self.repo))
        self.assertEqual(code, 0, error)
        status = json.loads(output)
        self.assertEqual(status["id"], "target")
        self.assertEqual(status["status"], "running")
        self.assertEqual(check.call_count, 1)


if __name__ == "__main__":
    unittest.main()
