"""Actual CLI approval, file scope, cancellation, and durable tool replay."""
import json
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import threading
import time
from unittest.mock import Mock, patch

from agent_council import cli, tool_access
from test_council import PROJECT_ROOT, TemporaryTest


def block(request):
    return "```council-tool\n" + json.dumps(request) + "\n```"


class ToolAccessTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.project = self.root / "project"
        self.project.mkdir()
        self.session = self.root / "session"
        self.session.mkdir()
        self.stop = threading.Event()

    def request(self):
        return {"tool": "shell", "argv": [sys.executable, "-c", "print('actual output')"],
                "cwd": str(self.project), "reason": "Inspect the project"}

    def broker(self, answer="y", **options):
        return tool_access.Broker(self.session, [self.project], run_process=cli.run_process,
                                  terminal=True, input_fn=lambda _: answer, emit=lambda _: None,
                                  stop=self.stop, **options)

    def test_only_an_entire_request_block_can_execute(self):
        request = self.request()
        self.assertEqual(tool_access.parse_request(block(request)), request)
        for answer in ("ordinary answer", "Example:\n" + block(request), block(request) + "\nexplanation"):
            self.assertIsNone(tool_access.parse_request(answer))
        for request in ({}, {"tool": "shell", "argv": [], "cwd": str(self.project), "reason": "x"},
                        {"tool": "list_dir", "path": "relative", "reason": "x"},
                        {"tool": "list_dir", "path": str(self.project), "reason": "x", "env": {}}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                tool_access.parse_request(block(request))

    def test_shell_runs_after_approval_and_replay_never_executes_it_twice(self):
        broker = self.broker()
        with patch.object(broker, "run_process", wraps=cli.run_process) as run:
            result = broker.execute(self.request(), agent="alpha", tag="round1", index=0)
            self.assertTrue(result["ok"])
            self.assertEqual(result["stdout"], "actual output\n")
            self.assertEqual(broker.execute(self.request(), agent="alpha", tag="round1", index=0), result)
            self.assertEqual(broker.execute(self.request(), agent="alpha", tag="round1", index=1), result)
            self.assertEqual(run.call_count, 1)

    def test_no_or_default_answer_never_executes_a_command(self):
        for answer in ("n", ""):
            broker = self.broker(answer)
            broker.run_process = Mock()
            result = broker.execute(self.request(), agent="alpha", tag=answer or "empty", index=0)
            self.assertFalse(result["ok"])
            broker.run_process.assert_not_called()

    def test_no_terminal_saves_pending_request_and_pauses(self):
        broker = self.broker()
        broker.terminal = False
        broker.run_process = Mock()
        with self.assertRaisesRegex(tool_access.ToolPause, "awaiting_tool_approval"):
            broker.execute(self.request(), agent="alpha", tag="round0", index=0)
        broker.run_process.assert_not_called()
        saved = json.loads(next((self.session / "tool-calls").glob("*.json")).read_text())
        self.assertEqual(saved["status"], "pending")
        self.assertEqual(saved["request"], self.request())

    def test_quit_stops_other_approvals_without_execution(self):
        broker = self.broker("q")
        broker.run_process = Mock()
        with self.assertRaisesRegex(tool_access.ToolPause, "stopped_by_user"):
            broker.execute(self.request(), agent="alpha", tag="round0", index=0)
        with self.assertRaises(tool_access.ToolPause):
            broker.execute(self.request(), agent="beta", tag="round0", index=0)
        broker.run_process.assert_not_called()

    def test_eof_pauses_without_execution(self):
        broker = self.broker()
        broker.input_fn = Mock(side_effect=EOFError)
        broker.run_process = Mock()
        with self.assertRaisesRegex(tool_access.ToolPause, "awaiting_tool_approval"):
            broker.execute(self.request(), agent="alpha", tag="round0", index=0)
        broker.run_process.assert_not_called()

    def test_denied_policy_needs_no_terminal_or_input(self):
        broker = self.broker(policy="deny")
        broker.terminal = False
        broker.input_fn = Mock()
        broker.run_process = Mock()
        self.assertFalse(broker.execute(self.request(), agent="alpha", tag="round0", index=0)["ok"])
        broker.input_fn.assert_not_called()
        broker.run_process.assert_not_called()

    def test_unknown_outcome_requires_a_new_explicit_approval(self):
        request = self.request()
        broker = self.broker()
        broker.execute(request, agent="alpha", tag="round0", index=0)
        record = next((self.session / "tool-calls").glob("*.json"))
        state = json.loads(record.read_text())
        state["status"] = "running"
        record.write_text(json.dumps(state))
        output = []
        broker = self.broker("n")
        broker.emit = output.append
        broker.run_process = Mock()
        result = broker.execute(request, agent="alpha", tag="round0", index=0)
        broker.run_process.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertTrue(any("outcome is unknown" in line for line in output))

    def test_file_access_checks_scope_and_preserves_line_numbers(self):
        source = self.project / "source.py"
        source.write_text("first\nsecond\nthird\n")
        request = tool_access.parse_request(block({"tool": "read_file", "path": str(source),
                                                  "start_line": 2, "max_lines": 1, "reason": "Read source"}))
        result = self.broker().execute(request, agent="alpha", tag="round1", index=0)
        self.assertEqual(result["text"], "2: second")
        self.assertTrue(result["truncated"])
        outside = self.root / "secret.txt"
        outside.write_text("private")
        (self.project / "escape.txt").symlink_to(outside)
        request["path"] = str(self.project / "escape.txt")
        broker = self.broker()
        broker.input_fn = Mock()
        self.assertFalse(broker.execute(request, agent="alpha", tag="round1", index=1)["ok"])
        broker.input_fn.assert_not_called()

    def test_timeout_kills_the_approved_process(self):
        request = self.request() | {"argv": [sys.executable, "-c", "import time; time.sleep(30)"]}
        result = self.broker(timeout=0.2).execute(request, agent="alpha", tag="round0", index=0)
        self.assertFalse(result["ok"])
        self.assertIn("timed out", result["error"])

    def test_signal_during_command_leaves_unknown_outcome_for_explicit_retry(self):
        broker = self.broker()
        def interrupted(*args, **kwargs):
            self.stop.set()
            return {"returncode": -9, "error": "interrupted", "stdout": "", "stderr": ""}
        broker.run_process = interrupted
        with self.assertRaisesRegex(tool_access.ToolPause, "interrupted"):
            broker.execute(self.request(), agent="alpha", tag="round0", index=0)
        record = json.loads(next((self.session / "tool-calls").glob("*.json")).read_text())
        self.assertEqual(record["status"], "running")
        self.assertTrue(record["approved"])

    def test_known_secrets_are_redacted_from_tool_outputs(self):
        with patch.dict(os.environ, COUNCIL_TEST_TOKEN="private-value-for-test"):
            self.assertNotIn("private-value-for-test", tool_access.scrub("x private-value-for-test y"))
        self.assertNotIn("sk-example123456789", tool_access.scrub("Bearer sk-example123456789"))

    def test_directory_flags_do_not_grant_codex_external_writes(self):
        for name in ("claude", "agy", "codex", "hermes", "custom"):
            agent = {"argv": [name]}
            tool_access.native_directories(agent, [self.project])
            self.assertEqual("--add-dir" in agent["argv"], name in ("claude", "agy"))
            self.assertNotIn("--dangerously-skip-permissions", agent["argv"])

    def test_native_permission_detection_excludes_authentication_and_model_refusals(self):
        self.assertTrue(tool_access.native_permission_error(
            {"ok": False, "failure_kind": "error", "error": "headless tool command permission denied"}))
        for kind, error in (("authentication", "403 tool permission denied"),
                            ("error", "model safety refusal"), ("error", "installation lock is read-only")):
            self.assertFalse(tool_access.native_permission_error({"ok": False, "failure_kind": kind, "error": error}))


class ApprovalIntegrationTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.project = self.root / "project"
        self.project.mkdir()
        self.sessions = self.root / "sessions"
        cfg = self.config()
        for agent in cfg["agents"]:
            agent["argv"][1] = str(Path(__file__).with_name("tool_agent.py"))
            agent["env"]["TOOL_FIXTURE_ROOT"] = str(self.project)
        self.config_file = self.write_config(cfg)
        self.environment = os.environ | {"COUNCIL_SKILLS_DIR": str(self.root / "empty-skills")}
        (self.root / "empty-skills").mkdir()

    def command(self, *args):
        return [sys.executable, str(PROJECT_ROOT / "council.py"), "--sessions-dir", str(self.sessions),
                "--non-interactive", "--quiet", *args]

    def start(self, *args):
        return subprocess.run(self.command("--config", str(self.config_file), "--workspace", str(self.project),
                                            *args, "Explain this tradeoff"), capture_output=True, text=True,
                              timeout=20, env=self.environment)

    def session(self):
        return next(self.sessions.iterdir())

    def test_pending_tool_request_pauses_before_any_execution(self):
        proc = self.start()
        self.assertEqual(proc.returncode, 75, proc.stdout + proc.stderr)
        self.assertFalse((self.project / "approved-count").exists())
        meta = json.loads((self.session() / "session.json").read_text())
        self.assertEqual(meta["pause_reason"], "awaiting_tool_approval")

    def test_native_tool_denial_redirects_to_council_approval(self):
        cfg = self.config()
        for agent in cfg["agents"]:
            agent["argv"][1] = str(Path(__file__).with_name("tool_agent.py"))
            agent["env"].update(TOOL_FIXTURE_ROOT=str(self.project), NATIVE_TOOL_DENIAL="1")
        self.config_file = self.write_config(cfg)
        proc = self.start()
        self.assertEqual(proc.returncode, 75, proc.stdout + proc.stderr)
        self.assertFalse((self.project / "approved-count").exists())
        self.assertEqual(json.loads((self.session() / "session.json").read_text())["pause_reason"],
                         "awaiting_tool_approval")

    def test_denial_returns_to_the_agent_without_running_command(self):
        proc = self.start("--tool-approval", "deny")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse((self.project / "approved-count").exists())
        meta = json.loads((self.session() / "session.json").read_text())
        self.assertEqual(meta["status"], "completed")
        self.assertTrue(list((self.session() / "rounds").glob("round0-tool1.*.prompt.txt")))

    def test_cli_terminal_approval_and_resume_execute_exactly_once(self):
        paused = self.start()
        self.assertEqual(paused.returncode, 75, paused.stdout + paused.stderr)
        master, slave = pty.openpty()
        proc = subprocess.Popen(self.command("--resume", str(self.session())), stdin=slave, stdout=slave,
                                stderr=slave, env=self.environment, close_fds=True)
        os.close(slave)
        captured, approved = b"", False
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.2)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    captured += chunk
                    if b"Allow this exact operation?" in captured and not approved:
                        self.assertFalse((self.project / "approved-count").exists())
                        os.write(master, b"y\n")
                        approved = True
                if proc.poll() is not None:
                    break
            self.assertEqual(proc.wait(timeout=3), 0, captured.decode(errors="replace"))
            self.assertTrue(approved, captured.decode(errors="replace"))
            self.assertEqual((self.project / "approved-count").read_text(), "1")
            resumed = subprocess.run(self.command("--resume", str(self.session())), capture_output=True,
                                     text=True, timeout=10, env=self.environment)
            self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
            self.assertEqual((self.project / "approved-count").read_text(), "1")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            os.close(master)

    def test_ctrl_c_cancels_the_terminal_approval(self):
        paused = self.start()
        self.assertEqual(paused.returncode, 75, paused.stdout + paused.stderr)
        master, slave = pty.openpty()
        proc = subprocess.Popen(self.command("--resume", str(self.session())), stdin=slave, stdout=slave,
                                stderr=slave, env=self.environment, close_fds=True)
        os.close(slave)
        captured = b""
        try:
            deadline = time.monotonic() + 10
            while b"Allow this exact operation?" not in captured and time.monotonic() < deadline:
                if select.select([master], [], [], 0.2)[0]:
                    captured += os.read(master, 65536)
            self.assertIn(b"Allow this exact operation?", captured)
            proc.send_signal(signal.SIGINT)
            self.assertEqual(proc.wait(timeout=3), 130)
            self.assertFalse((self.project / "approved-count").exists())
            self.assertEqual(json.loads((self.session() / "session.json").read_text())["status"], "interrupted")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            os.close(master)
