#!/usr/bin/env python3
"""Gemini review jobs for Codex, using an authenticated Antigravity CLI."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from prepare import capture_audit, capture_review, repo_root
from engine import atomic_json

ROOT = Path(__file__).resolve().parents[1]
MODELS = ("gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.1-pro-high")
TERMINAL = {"completed", "partial", "failed", "cancelled", "interrupted"}


def state_root():
    return Path(os.environ.get("GEMINI_PLUGIN_STATE", Path.home() / ".cache/gemini-codex/jobs")).resolve()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def workspace(path):
    path = Path(path).resolve(strict=True)
    try:
        return repo_root(path)
    except ValueError:
        return path


def status(job_dir):
    data = read_json(job_dir / "job.json")
    launch = job_dir / "launch.json"
    pid = data.get("pid") or (read_json(launch)["pid"] if launch.exists() else None)
    if data["status"] not in TERMINAL and pid:
        try:
            command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
        except OSError as exc:
            data.update(worker_check=f"Process status unavailable: {exc}", artifacts=str(job_dir))
            return data
        if command.returncode and command.stderr.strip():
            data["worker_check"] = f"Process status unavailable: {command.stderr.strip()}"
        elif command.returncode or str(job_dir) not in command.stdout:
            data.update(status="interrupted", error="Worker is no longer running; inspect worker.log")
    data["artifacts"] = str(job_dir)
    return data


def select_job(args):
    repo = str(workspace(args.repo))
    jobs = []
    if state_root().exists():
        for path in state_root().iterdir():
            if not (path / "job.json").is_file():
                continue
            data = read_json(path / "job.json")
            if data.get("repo") == repo and (not args.job or data.get("id") == args.job):
                jobs.append((path, status(path)))
    jobs.sort(key=lambda pair: pair[1]["created_at"], reverse=True)
    if not jobs:
        raise ValueError("No matching review job for this workspace")
    if args.command == "cancel" and not args.job:
        active = [pair for pair in jobs if pair[1]["status"] not in TERMINAL]
        if len(active) != 1:
            raise ValueError("Specify a job ID to cancel unless exactly one job is active")
        return active[0]
    return jobs[0]


def result(job_dir):
    data = status(job_dir)
    if data["status"] not in TERMINAL:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 2
    report = job_dir / "report.md"
    if report.exists():
        print(report.read_text(encoding="utf-8"))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"\nArtifacts: {job_dir}")
    return 0 if data["status"] == "completed" else 1


def start(args):
    if not shutil.which("agy"):
        raise ValueError("Antigravity CLI is missing. Install and log in with agy first.")
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").exists():
        raise ValueError("This release requires macOS sandbox-exec for repository write protection")
    request = (capture_review(args.repo, args.base, args.commit, args.path)
               if args.command == "review" else capture_audit(workspace(args.repo), args.documents, args.kind))
    repo = Path(request["repo"])
    if repo == Path.home() or repo == Path("/") or state_root().is_relative_to(repo):
        raise ValueError("Use a project directory that does not contain the plugin job store or your whole home")
    if not request["units"]:
        print(json.dumps({"scope": request["scope"], "status": "nothing_to_review" if not request["omitted"] else "incomplete",
                          "omitted": request["omitted"]}, ensure_ascii=False, indent=2))
        return 1 if request["omitted"] else 0
    request.update(model=args.model, timeout=args.timeout, goal=args.goal, focus=args.focus)
    job_id = uuid.uuid4().hex[:12]
    job_dir = state_root() / job_id
    job_dir.mkdir(parents=True, mode=0o700)
    os.chmod(job_dir, 0o700)
    now = datetime.now(timezone.utc).isoformat()
    job = {"id": job_id, "status": "starting", "created_at": now, "repo": str(repo),
           "model": args.model, "scope": request["scope"], "snapshot_sha256": request["snapshot_sha256"],
           "units": len(request["units"]), "omitted": request["omitted"]}
    for name, data in (("request.json", request), ("job.json", job)):
        atomic_json(job_dir / name, data)
    try:
        with (job_dir / "worker.log").open("w") as log:
            proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_run", str(job_dir)],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    except OSError as exc:
        atomic_json(job_dir / "job.json", dict(job, status="failed", error=f"Worker launch failed: {exc}"))
        raise
    # Keep parent launch metadata separate from the worker's atomic state updates.
    atomic_json(job_dir / "launch.json", {"pid": proc.pid})
    if args.background:
        print(json.dumps({"job_id": job_id, "pid": proc.pid, "status": "started", "scope": request["scope"],
                          "units": len(request["units"]), "artifacts": str(job_dir)}, ensure_ascii=False, indent=2))
        return 0
    print(f"Gemini job {job_id}: {len(request['units'])} input batches. Artifacts: {job_dir}", flush=True)
    try:
        while proc.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        (job_dir / "cancel.request").touch()
        print("Cancellation requested. Use status/result with job ID " + job_id)
        return 130
    return result(job_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    for action in ("review", "audit"):
        p = subs.add_parser(action)
        p.add_argument("--repo", default=".")
        p.add_argument("--model", choices=MODELS, default=MODELS[0])
        p.add_argument("--timeout", type=int, default=600, help="Seconds per model attempt")
        p.add_argument("--goal", default="")
        p.add_argument("--focus", default="")
        execution = p.add_mutually_exclusive_group()
        execution.add_argument("--background", action="store_true")
        execution.add_argument("--wait", action="store_true", help="Wait for result (default)")
        if action == "review":
            target = p.add_mutually_exclusive_group()
            target.add_argument("--base")
            target.add_argument("--commit")
            p.add_argument("--path", action="append", default=[])
        else:
            p.add_argument("documents", nargs="+")
            p.add_argument("--kind", choices=("plan", "spec"), default="plan")
    for action in ("status", "result", "cancel"):
        p = subs.add_parser(action)
        p.add_argument("job", nargs="?")
        p.add_argument("--repo", default=".")
    subs.add_parser("setup")
    worker = subs.add_parser("_run", help=argparse.SUPPRESS)
    worker.add_argument("job_dir", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "_run":
            from engine import run_job
            return run_job(args.job_dir)
        if args.command == "setup":
            if not shutil.which("agy"):
                raise ValueError("Install Antigravity CLI, then run agy interactively to log in: https://antigravity.google/docs/cli/installation/")
            check = subprocess.run(["agy", "models"], capture_output=True, text=True, timeout=60)
            print(json.dumps({"agy": shutil.which("agy"), "macos_write_guard": sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists(),
                              "models_exit_code": check.returncode, "models": check.stdout.strip(), "diagnostics": check.stderr.strip()}, ensure_ascii=False, indent=2))
            return 0 if check.returncode == 0 and sys.platform == "darwin" else 1
        if args.command in {"review", "audit"}:
            if args.timeout < 1:
                raise ValueError("--timeout must be positive")
            return start(args)
        job_dir, data = select_job(args)
        if args.command == "status":
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        if args.command == "result":
            return result(job_dir)
        if data["status"] in TERMINAL:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            (job_dir / "cancel.request").touch()
            print(json.dumps({"job_id": data["id"], "status": "cancellation_requested"}))
        return 0
    except (ValueError, OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        print(f"Gemini plugin: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
