"""Offline contract tests: fake agy processes exercise the actual job runner."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


ENGINE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "engine.py"
spec = importlib.util.spec_from_file_location("gemini_review_engine", ENGINE_PATH)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def review(complete=True, findings=None, limitations=None):
    return {"complete": complete, "summary": "已审查提供的材料。",
            "findings": findings or [], "limitations": limitations or []}


def finding(priority="P2"):
    return {"priority": priority, "title": "遗漏边界条件", "location": "src/app.py:12",
            "evidence": "空输入导致索引越界。", "recommendation": "先检查输入是否为空。"}


def success(value=None, **extra):
    return {"result": {"status": "SUCCESS", "structured_output": value or review(), **extra}}


def limit(conversation=None):
    result = {"status": "ERROR", "error": "Your previous response was cut off because it exceeded the output token limit"}
    if conversation:
        result["conversation_id"] = conversation
    return {"result": result}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gemini-engine-")
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.job = self.root / "job"
        self.job.mkdir()
        self.agy = self.root / "agy"
        self.sandbox = self.root / "sandbox-exec"
        self.sandbox.write_text(
            f"#!{sys.executable}\nimport json, os, pathlib, sys\n"
            "with (pathlib.Path(__file__).resolve().parent / 'sandbox-calls.jsonl').open('a') as log:\n"
            "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "os.execv(sys.argv[3], sys.argv[3:])\n")
        self.sandbox.chmod(0o700)
        self.agy.write_text(f"#!{sys.executable}\n" + r'''
import json, os, pathlib, subprocess, sys, time
root = pathlib.Path(__file__).resolve().parent
counter = root / "counter"
scenario = json.loads((root / "scenario.json").read_text())
with (root / "processes.jsonl").open("a") as log:
    log.write(json.dumps({"pid": os.getpid(), "argv": sys.argv}) + "\n")
initial = int(counter.read_text()) if counter.exists() else 0
print(json.dumps({"event": "init", "init": scenario[min(initial, len(scenario) - 1)].get("init", {})}), flush=True)
for payload in sys.stdin:
    index = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(index + 1))
    step = scenario[min(index, len(scenario) - 1)]
    (root / f"call-{index + 1}.json").write_text(json.dumps({"argv": sys.argv, "input": payload, "pid": os.getpid()}))
    if step.get("spawn_child"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (root / "child.pid").write_text(str(child.pid))
    if "stderr" in step:
        print(step["stderr"], file=sys.stderr, flush=True)
    if "before_sleep" in step:
        print(json.dumps(step["before_sleep"]), flush=True)
    if step.get("sleep"):
        time.sleep(step["sleep"])
    if "raw" in step:
        sys.stdout.write(step["raw"])
        sys.stdout.flush()
    elif "fragments" in step:
        for fragment in step["fragments"]:
            sys.stdout.write(fragment)
            sys.stdout.flush()
            time.sleep(0.01)
    else:
        print(json.dumps({"event": "result", "result": step["result"]}), flush=True)
    if step.get("after_sleep"):
        time.sleep(step["after_sleep"])
    if "late_stderr" in step:
        print(step["late_stderr"], file=sys.stderr, flush=True)
    if "exit" in step:
        sys.exit(step["exit"])
''', encoding="utf-8")
        self.agy.chmod(0o700)
        self.lookup = patch.object(engine.shutil, "which", side_effect=lambda name: str(self.root / name))
        self.lookup.start()
        self.addCleanup(self.lookup.stop)
        self.addCleanup(self.temporary.cleanup)

    def prepare(self, steps, units=None, **overrides):
        request = {"repo": str(self.repo), "model": "gemini-3.8-flash-high", "timeout": 3,
                   "kind": "diff", "scope": "uncommitted", "goal": "fix empty input",
                   "focus": "correctness", "units": units or [{"label": "src/app.py", "text": "12: return items[0]\n"}],
                   "omitted": [], "snapshot_sha256": "abc123"}
        request.update(overrides)
        (self.root / "scenario.json").write_text(json.dumps(steps))
        (self.job / "request.json").write_text(json.dumps(request))
        (self.job / "job.json").write_text(json.dumps({"id": "test-job", "status": "queued",
                                                      "created_at": "original", "custom": "keep"}))

    def run_job(self):
        code = engine.run_job(self.job)
        return code, json.loads((self.job / "report.json").read_text())

    def test_success_preserves_state_and_logs(self):
        step = success(model="observed-model", conversation_id="c1", usage={"total_tokens": 42})
        step["stderr"] = "provider diagnostic"
        self.prepare([step])
        code, report = self.run_job()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["snapshot_sha256"], "abc123")
        attempt = report["attempts"][0]
        self.assertEqual(attempt["model"], "observed-model")
        self.assertEqual(attempt["conversation_id"], "c1")
        self.assertEqual(attempt["usage"], {"total_tokens": 42})
        self.assertIn("provider diagnostic", (self.job / attempt["stderr"]).read_text())
        self.assertIn('"event": "result"', (self.job / attempt["events"]).read_text())
        state = json.loads((self.job / "job.json").read_text())
        self.assertEqual(state["created_at"], "original")
        self.assertEqual(state["custom"], "keep")
        self.assertEqual(state["pid"], os.getpid())
        argv = json.loads((self.root / "call-1.json").read_text())["argv"]
        self.assertIn("--disable-slash-commands", argv)
        self.assertIn("--json-schema", argv)

    def test_integration_can_locate_complete_captured_inputs_without_inline_duplication(self):
        units = [
            {"label": "catalog.json / chunk 1", "text": "catalog.json new 101: 独立批次甲\r\n" * 100},
            {"label": "catalog.json / chunk 2", "text": "catalog.json new 201: 独立批次乙\n" * 100},
        ]
        self.prepare([success()], units=units)
        code, report = self.run_job()
        self.assertEqual(code, 0)
        inputs = (self.job / "inputs").resolve()
        manifest = json.loads((inputs / "manifest.json").read_text())
        self.assertEqual(manifest["repo"], str(self.repo.resolve()))
        self.assertEqual(manifest["snapshot_sha256"], "abc123")
        self.assertEqual({path.name for path in inputs.iterdir()},
                         {"manifest.json", "unit-0001.txt", "unit-0002.txt"})
        for index, (unit, entry) in enumerate(zip(units, manifest["units"]), 1):
            self.assertEqual(entry["label"], unit["label"])
            path = Path(entry["snapshot_path"])
            self.assertEqual(path, inputs / f"unit-{index:04d}.txt")
            self.assertEqual(path.read_bytes(), unit["text"].encode("utf-8"))
            self.assertEqual(report["units"][index - 1]["snapshot_path"], str(path))

        def prompt(number):
            call = json.loads((self.root / f"call-{number}.json").read_text())
            return json.loads(call["input"])["message"]["content"]

        first, integration = prompt(1), prompt(3)
        context = json.loads(next(line.removeprefix("Review context: ") for line in first.splitlines()
                                  if line.startswith("Review context: ")))
        self.assertEqual(context["repo"], str(self.repo.resolve()))
        self.assertEqual(context["input_manifest"], str(inputs / "manifest.json"))
        self.assertNotIn("独立批次乙", first)
        summaries = json.loads(integration.split("<review_material>\n", 1)[1].rsplit("\n</review_material>", 1)[0])
        self.assertEqual([item["snapshot_path"] for item in summaries],
                         [entry["snapshot_path"] for entry in manifest["units"]])
        self.assertNotIn("独立批次甲", integration)
        self.assertNotIn("独立批次乙", integration)

    def test_added_workspaces_are_only_the_reviewed_repo_and_protected_inputs(self):
        self.prepare([success()])
        code, _ = self.run_job()
        self.assertEqual(code, 0)
        inputs = (self.job / "inputs").resolve()
        argv = json.loads((self.root / "call-1.json").read_text())["argv"]
        added = [argv[index + 1] for index, argument in enumerate(argv) if argument == "--add-dir"]
        self.assertCountEqual(added, [str(self.repo.resolve()), str(inputs)])
        sandbox_args = json.loads((self.root / "sandbox-calls.jsonl").read_text().splitlines()[0])
        profile = sandbox_args[1]
        self.assertIn(f"(deny file-write* (subpath {json.dumps(str(inputs))}))", profile)
        self.assertIn(f"(deny file-write* (subpath {json.dumps(str(self.repo.resolve()))}))", profile)

    def test_exit_zero_status_error_is_failure_without_retry(self):
        self.prepare([{"result": {"status": "ERROR", "error": "quota exhausted"}}])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(len(report["attempts"]), 1)
        self.assertEqual(report["attempts"][0]["process_exit"], 0)
        self.assertIn("quota exhausted", report["error"])
        self.assertNotIn("未发现可证实的问题", (self.job / "report.md").read_text())

    def test_nonzero_exit_cannot_succeed(self):
        self.prepare([dict(success(), exit=7)])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(len(report["attempts"]), 1)

    def test_malformed_stream_is_not_a_clean_review(self):
        self.prepare([{"raw": "{broken json}\n"}])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(report["attempts"][0]["error_kind"], "malformed")
        self.assertEqual(len(report["attempts"]), 1)

    def test_invalid_structured_output_not_replaced_by_response(self):
        self.prepare([success({"complete": True}, response=json.dumps(review()))])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(report["attempts"][0]["error_kind"], "schema")

    def test_success_with_denied_actions_stops_without_retry_or_schema_fallback(self):
        denied = [{"action": "read_file", "display_name": "ViewFile"}]
        for response in ("", json.dumps(review())):
            with self.subTest(response=response):
                self.prepare([{"result": {"status": "SUCCESS", "response": response,
                                           "denied_actions": denied}}],
                             units=[{"label": "a", "text": "a:1 change\n" * 2000},
                                    {"label": "b", "text": "b:1 change\n"}])
                code, report = self.run_job()
                self.assertEqual(code, 1)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(len(report["attempts"]), 1)
                attempt = report["attempts"][0]
                self.assertEqual(attempt["process_exit"], 0)
                self.assertEqual(attempt["result_status"], "SUCCESS")
                self.assertEqual(attempt["error_kind"], "permission")
                self.assertEqual(attempt["result"]["denied_actions"], denied)
                self.assertIn("ViewFile", report["error"])
                self.assertEqual(report["unreviewed_units"], ["b"])
                self.assertEqual(report["findings"], [])

    def test_response_json_fallback(self):
        self.prepare([{"result": {"status": "SUCCESS", "response": json.dumps(review())}}])
        code, report = self.run_job()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "completed")

    def test_output_limit_continues_once_and_does_not_sum_usage(self):
        first = limit("conversation-one")
        first["result"]["usage"] = {"total_tokens": 100}
        self.prepare([first, success(usage={"total_tokens": 180})])
        code, report = self.run_job()
        self.assertEqual(code, 0)
        self.assertEqual([a["usage"]["total_tokens"] for a in report["attempts"]], [100, 180])
        self.assertTrue(report["attempts"][1]["usage_may_be_cumulative"])
        self.assertNotIn("total_usage", report)
        argv = json.loads((self.root / "call-2.json").read_text())["argv"]
        self.assertEqual(argv[-2:], ["--conversation", "conversation-one"])
        self.assertEqual(report["attempts"][0]["error_kind"], "output_limit")
        self.assertEqual(len(report["sessions"]), 2)
        self.assertNotEqual(report["attempts"][0]["child_pid"], report["attempts"][1]["child_pid"])

    def test_continuation_quota_error_does_not_split_or_retry(self):
        self.prepare([limit("c1"), {"result": {"status": "ERROR", "error": "quota exhausted"}}],
                     units=[{"label": "large", "text": "123456789\n" * 2000}])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(len(report["attempts"]), 2)

    def test_output_limit_splits_preserving_coverage_and_deduplicates(self):
        material = "src/app.py new 12: a long changed source line\n" * 500
        self.prepare([limit("c1"), limit("c1"), success(review(findings=[finding("P2")])),
                      success(review(findings=[finding("P1")]))],
                     units=[{"label": "large", "text": material}])
        code, report = self.run_job()
        self.assertEqual(code, 0)
        self.assertEqual(len(report["attempts"]), 5)
        self.assertEqual(report["coverage"]["completed"], 1)
        self.assertEqual(report["coverage"]["integration"], "completed")
        self.assertEqual(report["findings"], [finding("P1")])
        halves = engine.split_material(material)
        self.assertEqual("".join(halves), material)
        self.assertEqual(len(report["units"][0]["children"]), 2)
        self.assertEqual(len(report["sessions"]), 3)
        self.assertEqual([item["session_id"] for item in report["attempts"]],
                         ["session-1", "session-2", "session-3", "session-3", "session-3"])
        processes = [json.loads(line) for line in (self.root / "processes.jsonl").read_text().splitlines()]
        self.assertNotIn("--conversation", processes[2]["argv"])

    def test_split_recursion_is_bounded(self):
        self.prepare([limit()], units=[{"label": "large", "text": "abcdefghij\n" * 4000}])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(len(report["attempts"]), 7)
        self.assertEqual(report["status"], "failed")

    def test_incomplete_model_result_is_not_completed(self):
        self.prepare([success(review(complete=False))])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertNotEqual(report["status"], "completed")
        self.assertTrue(report["limitations"])

    def test_omitted_material_prevents_completed_status(self):
        self.prepare([success()], omitted=["generated.dat is omitted"])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "partial")
        self.assertIn("generated.dat is omitted", report["limitations"])

    def test_integration_failure_marks_partial(self):
        self.prepare([success(), success(), {"result": {"status": "ERROR", "error": "auth expired"}}],
                     units=[{"label": "a", "text": "a:1 change\n"}, {"label": "b", "text": "b:1 change\n"}])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["coverage"], {"total": 2, "completed": 2, "integration": "incomplete"})
        self.assertEqual(len(report["attempts"]), 3)

    def test_integration_success_is_required_for_multiple_units(self):
        self.prepare([success(), success(), success(review(findings=[finding()]))],
                     units=[{"label": "a", "text": "a:1 change\n"}, {"label": "b", "text": "b:1 change\n"}])
        code, report = self.run_job()
        self.assertEqual(code, 0)
        self.assertEqual(report["coverage"]["integration"], "completed")
        self.assertEqual(report["findings"], [finding()])
        prompt = json.loads(json.loads((self.root / "call-3.json").read_text())["input"])["message"]["content"]
        self.assertIn("cross-file interactions", prompt)
        self.assertIn("snapshot is authoritative", prompt)

    def test_all_batches_and_integration_reuse_one_stream_process(self):
        steps = [success(usage={"total_tokens": value}, duration_seconds=value / 10, num_turns=i)
                 for i, value in enumerate((10, 20, 30, 40), 1)]
        steps[0]["init"] = {"conversation_id": "init-conversation", "model": "observed-model"}
        steps[0].update(after_sleep=0.08, late_stderr="late first-round diagnostic")
        steps[-1].update(after_sleep=0.05, late_stderr="late final-round diagnostic")
        self.prepare(steps, units=[{"label": str(i), "text": f"file-{i}:1 change"} for i in range(3)])
        code, report = self.run_job()
        self.assertEqual(code, 0)
        processes = [json.loads(line) for line in (self.root / "processes.jsonl").read_text().splitlines()]
        self.assertEqual(len(processes), 1)
        self.assertNotIn("--conversation", processes[0]["argv"])
        attempts = report["attempts"]
        self.assertEqual([item["session_turn"] for item in attempts], [1, 2, 3, 4])
        self.assertEqual({item["session_id"] for item in attempts}, {"session-1"})
        self.assertEqual({item["conversation_id"] for item in attempts}, {"init-conversation"})
        self.assertEqual([item["usage"]["total_tokens"] for item in attempts], [10, 20, 30, 40])
        self.assertEqual([item["duration_seconds"] for item in attempts], [1, 2, 3, 4])
        self.assertTrue(all(item["usage_may_be_cumulative"] for item in attempts))
        self.assertTrue(all(item["process_exit"] == 0 for item in attempts))
        for attempt in attempts:
            events = [json.loads(line) for line in (self.job / attempt["events"]).read_text().splitlines()]
            self.assertEqual(sum(event["event"] == "result" for event in events), 1)
        session = report["sessions"][0]
        self.assertEqual({item["stderr"] for item in attempts}, {session["stderr"]})
        self.assertEqual({item["stderr_scope"] for item in attempts}, {"session"})
        self.assertEqual(list(self.job.glob("attempt-*.stderr.log")), [])
        diagnostics = (self.job / session["stderr"]).read_text()
        self.assertIn("late first-round diagnostic", diagnostics)
        self.assertIn("late final-round diagnostic", diagnostics)
        self.assertEqual(session["conversation_id"], "init-conversation")
        self.assertEqual(session["usage"], {"total_tokens": 40})
        events = [json.loads(line) for line in (self.job / session["events"]).read_text().splitlines()]
        self.assertEqual(sum(event["event"] == "init" for event in events), 1)

    def test_fragmented_ndjson_result_is_reassembled(self):
        event = json.dumps({"event": "result", "result": success()["result"]}, ensure_ascii=False) + "\n"
        self.prepare([{"fragments": [event[:19], event[19:71], event[71:]]}])
        code, report = self.run_job()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "completed")

    def test_eof_without_result_is_failure_even_with_exit_zero(self):
        self.prepare([{"raw": "", "exit": 0}])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(report["attempts"][0]["error_kind"], "process")
        self.assertIn("without a result", report["error"])

    def test_truncated_ndjson_at_eof_is_not_carried_to_another_turn(self):
        self.prepare([{"raw": '{"event":"result","result":', "exit": 0}])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(report["attempts"][0]["error_kind"], "malformed")
        self.assertIn("truncated", report["error"])

    def test_duplicate_result_cannot_become_the_next_batch_result(self):
        event = json.dumps({"event": "result", "result": success()["result"]}) + "\n"
        self.prepare([{"raw": event + event}])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(report["attempts"][0]["error_kind"], "malformed")
        self.assertEqual(report["coverage"]["completed"], 0)

    def test_stale_cumulative_turn_counter_is_rejected(self):
        self.prepare([success(num_turns=1), success(num_turns=1)],
                     units=[{"label": "a", "text": "a:1 change"}, {"label": "b", "text": "b:1 change"}])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["attempts"][1]["error_kind"], "malformed")
        self.assertIn("Stale result", report["error"])

    def test_schema_is_validated_on_each_turn_before_integration(self):
        self.prepare([success(), success({"complete": True})],
                     units=[{"label": "a", "text": "a:1 change"}, {"label": "b", "text": "b:1 change"}])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["coverage"]["completed"], 1)
        self.assertEqual(report["coverage"]["integration"], "not_run")
        self.assertEqual(report["attempts"][1]["error_kind"], "schema")
        self.assertEqual(len(report["attempts"]), 2)
        self.assertEqual(len(report["sessions"]), 1)

    def test_second_turn_timeout_keeps_first_result_and_cleans_processes(self):
        self.prepare([success(usage={"total_tokens": 10}), {"sleep": 30, "spawn_child": True}],
                     units=[{"label": "a", "text": "a:1 change"}, {"label": "b", "text": "b:1 change"}],
                     timeout=1.5)
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(report["coverage"]["completed"], 1)
        self.assertEqual(report["attempts"][1]["error_kind"], "timeout")
        self.assertIsNone(report["attempts"][1]["usage"])
        self.assertEqual(len(report["sessions"]), 1)
        self.assert_process_gone(int((self.root / "child.pid").read_text()))
        self.assert_process_gone(report["sessions"][0]["pid"])

    def test_one_failed_unit_cannot_be_clean_and_skips_integration(self):
        self.prepare([success(), {"raw": "oops\n"}],
                     units=[{"label": "a", "text": "a:1 change\n"}, {"label": "b", "text": "b:1 change\n"}])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["coverage"]["integration"], "not_run")
        self.assertEqual(len(report["attempts"]), 2)

    def test_auth_or_quota_failure_stops_remaining_units(self):
        for error in ("quota exhausted", "authentication token expired"):
            with self.subTest(error=error):
                self.prepare([{"result": {"status": "ERROR", "error": error}}],
                             units=[{"label": "a", "text": "a:1 change\n"},
                                    {"label": "b", "text": "b:1 change\n"}])
                code, report = self.run_job()
                self.assertEqual(code, 1)
                self.assertEqual(len(report["attempts"]), 1)
                self.assertEqual(report["unreviewed_units"], ["b"])
                self.assertIn("b: not reviewed", "\n".join(report["limitations"]))

    def test_nonzero_exit_without_result_stops_remaining_units(self):
        self.prepare([{"raw": "", "exit": 2, "stderr": "unsupported CLI argument"}],
                     units=[{"label": "a", "text": "a:1 change\n"},
                            {"label": "b", "text": "b:1 change\n"}])
        code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertEqual(len(report["attempts"]), 1)
        self.assertEqual(report["attempts"][0]["error_kind"], "process")
        self.assertIn("unsupported CLI argument", report["error"])
        self.assertIn("session stderr (may include other turns)", report["error"])
        self.assertEqual(report["unreviewed_units"], ["b"])

    def test_timeout_kills_child_process_group_and_keeps_stream(self):
        self.prepare([{"sleep": 30, "spawn_child": True, "before_sleep": {"event": "progress", "message": "working"}}],
                     timeout=1.5)
        started = time.monotonic()
        code, report = self.run_job()
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(code, 1)
        self.assertEqual(report["attempts"][0]["error_kind"], "timeout")
        self.assertIn("working", (self.job / "attempt-1.events.jsonl").read_text())
        self.assert_process_gone(int((self.root / "child.pid").read_text()))
        self.assert_process_gone(report["attempts"][0]["child_pid"])

    def test_cancellation_kills_child_process_group(self):
        self.prepare([{"sleep": 30, "spawn_child": True}], timeout=10)

        def cancel():
            deadline = time.monotonic() + 3
            while not (self.root / "child.pid").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            (self.job / "cancel.request").touch()

        thread = threading.Thread(target=cancel)
        thread.start()
        code, report = self.run_job()
        thread.join(timeout=3)
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "cancelled")
        self.assertEqual(report["attempts"][0]["error_kind"], "cancelled")
        self.assert_process_gone(int((self.root / "child.pid").read_text()))
        self.assert_process_gone(report["attempts"][0]["child_pid"])

    def test_missing_sandbox_fails_without_running_agy(self):
        self.prepare([success()])
        with patch.object(engine.shutil, "which", return_value=None):
            code, report = self.run_job()
        self.assertEqual(code, 1)
        self.assertIn("sandbox-exec", report["error"])
        self.assertEqual(report["attempts"], [])

    def test_sandbox_profile_resolves_paths_and_quotes_sbpl(self):
        directory = self.root / 'a "quoted" directory'
        directory.mkdir()
        link = self.root / "link"
        link.symlink_to(directory, target_is_directory=True)
        metadata = self.root / "metadata"
        metadata.mkdir()
        profile = engine.sandbox_profile([str(link), str(metadata)])
        self.assertIn(json.dumps(str(directory.resolve())), profile)
        self.assertIn(json.dumps(str(metadata.resolve())), profile)
        self.assertNotIn(json.dumps(str(link)), profile)
        self.assertEqual(profile.count("deny file-write*"), 2)

    def test_validation_rejects_invalid_types_lengths_and_empty_fields(self):
        cases = []
        item = review(); item["complete"] = "yes"; cases.append(item)
        item = review(); item["summary"] = " "; cases.append(item)
        item = review(); item["summary"] = "x" * 1001; cases.append(item)
        item = review(findings=[finding()] * 9); cases.append(item)
        item = review(findings=[finding()]); item["findings"][0]["priority"] = "P0"; cases.append(item)
        item = review(findings=[finding()]); item["findings"][0]["evidence"] = ""; cases.append(item)
        item = review(); item["limitations"] = "none"; cases.append(item)
        item = review(); item["extra"] = True; cases.append(item)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                engine.validate_review(value)

    def assert_process_gone(self, pid):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)
        # Linux containers may leave an orphan zombie until PID 1 reaps it.
        proc_status = Path(f"/proc/{pid}/status")
        if proc_status.exists() and "State:\tZ" in proc_status.read_text():
            return
        self.fail(f"process {pid} survived cancellation/timeout")


if __name__ == "__main__":
    unittest.main()
