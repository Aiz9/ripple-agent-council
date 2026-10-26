import json
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import tempfile
import time
import unittest

import yaml

from agent_council.recovery import capacity_answer, failure_kind

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("recovery_agent.py").resolve()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.control = self.root / "control.json"
        self.control.write_text("{}")
        self.sessions = self.root / "sessions"
        self.config = self.root / "config.yaml"
        self.cfg = {"settings": {"max_rounds": 3, "retry_delay_sec": 0.01},
                    "agents": [{"name": name, "argv": [sys.executable, str(FIXTURE), name, "{prompt}"],
                                "env": {"FIXTURE_STATE": str(self.root)}} for name in ("alpha", "beta", "gamma")]}

    def command(self, *extra):
        return [sys.executable, str(PROJECT / "council.py"), "--sessions-dir", str(self.sessions),
                "--quiet", "--non-interactive", *extra]

    def start(self, *extra):
        self.config.write_text(yaml.safe_dump(self.cfg))
        return subprocess.run(self.command("--config", str(self.config), *extra, "Discuss this request"),
                              capture_output=True, text=True, timeout=15)

    def resume(self, spec="latest", *extra):
        return subprocess.run(self.command("--resume", str(spec), *extra), capture_output=True, text=True, timeout=15)

    def session(self):
        return next(p for p in self.sessions.iterdir() if p.is_dir())

    def meta(self):
        return json.loads((self.session() / "session.json").read_text())

    def count(self, key):
        path = self.root / (key + ".count")
        return int(path.read_text()) if path.exists() else 0

    def test_limit_notice_exit_zero_is_not_a_success(self):
        self.assertTrue(capacity_answer("You’ve hit your usage limit. Upgrade or try later."))
        self.assertFalse(capacity_answer("HTTP 429 means a rate limit; here is a handling strategy."))
        self.assertEqual(failure_kind("", '{"error":{"code":429}}'), "capacity")

    def test_one_limited_peer_does_not_end_the_task(self):
        self.control.write_text(json.dumps({"fail": {"round1.gamma": "capacity"}}))
        process = self.start()
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.meta()["status"], "completed")
        self.assertIn("gamma", self.meta()["dropped"])
        self.assertEqual(self.count("round1.gamma"), 1)

    def test_transient_failure_retries_but_retains_attempt_logs(self):
        self.control.write_text(json.dumps({"fail": {"round1.gamma": "transient_once"}}))
        process = self.start()
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertEqual(self.count("round1.gamma"), 2)
        attempts = [json.loads(p.read_text()) for p in (self.session() / "attempts").glob("*.json")]
        gamma = [a for a in attempts if a["tag"] == "round1" and a["name"] == "gamma"]
        self.assertEqual(len(gamma), 2)
        for attempt in gamma:
            artifact = attempt["result"]["artifact_tag"]
            self.assertTrue((self.session() / "rounds" / f"{artifact}.gamma.stdout.txt").is_file())

    def test_quorum_shortage_pauses_then_resumes_without_repaying_successes(self):
        self.control.write_text(json.dumps({"fail": {"round1.beta": "capacity", "round1.gamma": "capacity"}}))
        process = self.start()
        self.assertEqual(process.returncode, 75, process.stdout)
        self.assertEqual(self.meta()["status"], "paused")
        self.assertEqual(self.meta()["pause_reason"], "quorum_lost")
        self.control.write_text("{}")
        # Resume must use its own snapshot even when the original config is gone.
        self.config.unlink()
        process = self.resume()
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.count("round0.alpha"), 1)
        self.assertEqual(self.count("round1.alpha"), 1)
        self.assertEqual(self.count("round1.beta"), 2)
        self.assertEqual(self.meta()["resume_count"], 1)
        self.assertGreaterEqual(self.meta()["reused_calls"], 2)
        self.assertEqual(len(list(self.sessions.iterdir())), 1)

    def test_limited_hierarchical_leader_fails_over_transparently(self):
        self.cfg["settings"].update(mode="hierarchical", leader="alpha")
        self.control.write_text(json.dumps({"fail": {"plan1.alpha": "capacity"}}))
        process = self.start()
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.meta()["leader"], "beta")
        self.assertEqual(self.meta()["synthesizer"], "beta")
        self.assertEqual(self.meta()["leader_changes"], [{"from": "alpha", "to": "beta", "phase": "plan1"}])

    def test_limited_leader_review_can_promote_a_specialist_without_duplicate_vote(self):
        self.cfg["settings"].update(mode="hierarchical", leader="alpha")
        self.control.write_text(json.dumps({"fail": {"review1.alpha": "capacity"}}))
        process = self.start()
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertEqual(self.meta()["rounds"][0]["ok"], ["gamma", "beta"])
        self.assertEqual(self.count("round1.beta"), 1)
        self.assertEqual(self.count("review1.beta"), 1)

    def test_completed_session_resume_is_idempotent(self):
        self.assertEqual(self.start().returncode, 0)
        counts = {p.name: p.read_text() for p in self.root.glob("*.count")}
        result = self.resume(self.session())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already complete", result.stdout)
        self.assertEqual(counts, {p.name: p.read_text() for p in self.root.glob("*.count")})

    def test_completed_answer_is_recovered_when_markdown_is_missing(self):
        self.assertEqual(self.start().returncode, 0)
        (self.session() / "final.md").unlink()
        result = self.resume(self.session())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Completed with retained evidence", (self.session() / "final.md").read_text())
        self.assertEqual(self.count("final.alpha"), 1)

    @unittest.skipUnless(os.name == "posix", "signal interruption")
    def test_interrupted_framing_is_retried_instead_of_sealed(self):
        self.control.write_text(json.dumps({"hang": ["round0.alpha"]}))
        self.config.write_text(yaml.safe_dump(self.cfg))
        process = subprocess.Popen(self.command("--config", str(self.config), "Discuss this request"),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 8
            while not (self.root / "round0.alpha.pid").exists():
                if time.monotonic() > deadline:
                    self.fail("framing did not start")
                time.sleep(0.02)
            process.send_signal(signal.SIGINT)
            process.communicate(timeout=5)
            self.assertEqual(process.returncode, 130)
            self.control.write_text("{}")
            result = self.resume()
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(self.count("round0.alpha"), 2)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_build_retry_is_explicit_and_preserves_repaired_files(self):
        result = self.start("--type", "build")
        self.assertEqual(result.returncode, 1, result.stdout)
        workspace = self.session() / "workspace"
        self.assertEqual((workspace / "runs").read_text(), "1")
        final_count = self.count("final.alpha")
        result = self.resume()
        self.assertEqual(result.returncode, 1)
        self.assertEqual((workspace / "runs").read_text(), "1")
        (workspace / "fixed").touch()
        (workspace / "test.py").write_text("print('user repaired this file')")
        result = self.resume("latest", "--retry-build")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((workspace / "test.py").read_text(), "print('user repaired this file')")
        self.assertEqual(self.count("final.alpha"), final_count)

    def test_unknown_build_outcome_does_not_repeat_shell_command(self):
        self.assertEqual(self.start("--type", "build").returncode, 1)
        (self.session() / "build-step.json").write_text('{"status":"running"}')
        result = self.resume()
        self.assertEqual(result.returncode, 75, result.stdout)
        self.assertEqual(self.meta()["pause_reason"], "build_outcome_unknown")
        self.assertEqual((self.session() / "workspace/runs").read_text(), "1")

    def test_cached_agent_work_can_resume_after_executable_disappears(self):
        executable = self.root / "agent-wrapper"
        executable.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
        executable.chmod(0o755)
        for agent in self.cfg["agents"]:
            agent["argv"][0] = str(executable)
        result = self.start("--type", "build")
        self.assertEqual(result.returncode, 1, result.stdout)
        executable.unlink()
        (self.session() / "workspace/fixed").touch()
        result = self.resume("latest", "--retry-build")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.count("final.alpha"), 1)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux abrupt process recovery")
    def test_sigkill_mid_parallel_round_reuses_finished_calls_and_recovers_child(self):
        self.control.write_text(json.dumps({"hang": ["round1.beta"]}))
        self.config.write_text(yaml.safe_dump(self.cfg))
        process = subprocess.Popen(self.command("--config", str(self.config), "Discuss this request"),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        orphan = None
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                completed = list(self.sessions.glob("*/calls/round1.*.json"))
                if len(completed) >= 2 and (self.root / "round1.beta.pid").exists():
                    break
                time.sleep(0.02)
            else:
                self.fail("parallel calls did not reach checkpoint")
            orphan = int((self.root / "round1.beta.pid").read_text())
            process.kill()
            process.communicate(timeout=5)
            self.control.write_text("{}")
            resumed = self.resume()
            self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
            self.assertEqual(self.count("round1.alpha"), 1)
            self.assertEqual(self.count("round1.gamma"), 1)
            self.assertEqual(self.count("round1.beta"), 2)
            self.assertEqual(self.meta()["status"], "completed")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            if orphan:
                try:
                    os.killpg(orphan, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_session_listing_and_resume_reject_new_goal(self):
        self.control.write_text(json.dumps({"fail": {"round1.beta": "capacity", "round1.gamma": "capacity"}}))
        self.start()
        listing = subprocess.run(self.command("--sessions"), capture_output=True, text=True)
        self.assertIn(self.session().name, listing.stdout)
        self.assertIn("paused", listing.stdout)
        result = self.resume("latest", "new goal")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
