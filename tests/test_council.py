import io
import json
import os
from pathlib import Path
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import yaml

from agent_council import cli as council

PROJECT_ROOT = Path(__file__).resolve().parents[1]

FIXTURE = Path(__file__).with_name("fake_agent.py").resolve()


def make_claude_home(home: Path, top_level_json: bool = True) -> Path:
    """A fake HOME with Claude configuration files; contents are fixture data."""
    (home / ".claude").mkdir(parents=True)
    (home / ".claude/.credentials.json").write_text('{"token": "fixture"}')
    (home / ".claude/settings.json").write_text("{}")
    (home / ".claude/.claude.json").write_text('{"nested": true}')
    if top_level_json:
        (home / ".claude.json").write_text('{"top": true}')
    return home


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        council.STOP.clear()

    def config(self, scenario="question"):
        agents = []
        for name, mode in (("alpha", "claude_json"), ("beta", "file"), ("gamma", "stdout")):
            argv = [sys.executable, str(FIXTURE), name, mode, "{prompt}"]
            if mode == "file":
                argv.append("{outfile}")
            agents.append({"name": name, "output_mode": mode, "argv": argv,
                           "env": {"SCENARIO": scenario}})
        return {"settings": {"max_rounds": 3, "per_agent_timeout_sec": 5}, "agents": agents}

    def write_config(self, cfg):
        path = self.root / "agents.yaml"
        path.write_text(yaml.safe_dump(cfg))
        return path


class ConfigurationTests(TemporaryTest):
    def test_timeout_inheritance(self):
        config = council.load_config(self.write_config(self.config()))
        self.assertEqual(config["agents"][0]["timeout_sec"], 5)

    def test_invalid_configuration(self):
        mutations = [
            lambda c: c["settings"].update(max_rounds=0),
            lambda c: c["settings"].update(max_rounds=True),
            lambda c: c["settings"].update(per_agent_timeout_sec=float("nan")),
            lambda c: c["settings"].update(min_agents=1),
            lambda c: c["settings"].update(synthesizer="missing"),
            lambda c: c["settings"].update(verify_build="false"),
            lambda c: c["settings"].update(typo=3),
            lambda c: c["agents"][1].update(name="alpha"),
            lambda c: c["agents"][0].update(name="../escape"),
            lambda c: c["agents"][0].update(argv="echo prompt"),
            lambda c: c["agents"][0].update(argv=["python", "--prompt={prompt}"]),
            lambda c: c["agents"][0].update(model="ignored-model"),
            lambda c: c["agents"][0].update(env={"KEY": 3}),
            lambda c: c["agents"][0].update(output_mode="unknown"),
            lambda c: c["agents"][0].update(output_mode="file"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                cfg = self.config()
                mutate(cfg)
                with self.assertRaises(council.ConfigError):
                    council.load_config(self.write_config(cfg))

    def test_empty_config(self):
        with self.assertRaises(council.ConfigError):
            council.load_config(self.write_config(None))

    def test_hermes_preserves_configured_model(self):
        cfg = council.load_config(council.ROOT / "agents.yaml")
        hermes = next(a for a in cfg["agents"] if a["name"] == "hermes")
        self.assertEqual(council.build_argv(hermes, "hello", None), ["hermes", "-z", "hello"])

    def test_argv_prompt_is_literal_and_model_optional(self):
        agent = {"argv": ["tool", "--model", "{model}", "{prompt}"], "model": ""}
        prompt = "$(touch do-not-create) ' spaces\nnew line"
        self.assertEqual(council.build_argv(agent, prompt, None), ["tool", prompt])
        agent["model"] = "configured-model"
        self.assertEqual(council.build_argv(agent, prompt, None), ["tool", "--model", "configured-model", prompt])

    def test_prompt_transport_validation(self):
        cfg = self.config()
        cfg["agents"][0]["argv"][-1] = "-"
        cfg["agents"][0]["prompt_via"] = "stdin"
        loaded = council.load_config(self.write_config(cfg))
        self.assertEqual([a["prompt_via"] for a in loaded["agents"]], ["stdin", "argv", "argv"])
        with_prompt = [sys.executable, str(FIXTURE), "alpha", "claude_json", "{prompt}"]
        for change in ({"prompt_via": "pipe"},                          # unknown transport
                       {"prompt_via": "stdin", "argv": with_prompt},    # prompt sent twice
                       {"prompt_via": "argv"}):                         # argv without {prompt}
            with self.subTest(change=change):
                broken = self.config()
                broken["agents"][0]["argv"][-1] = "-"
                broken["agents"][0].update(change)
                with self.assertRaises(council.ConfigError):
                    council.load_config(self.write_config(broken))

    def test_bundled_transports_avoid_argument_limit_where_supported(self):
        cfg = council.load_config(council.ROOT / "agents.yaml")
        self.assertEqual({a["name"]: a["prompt_via"] for a in cfg["agents"]},
                         {"claude": "stdin", "codex": "stdin", "agy": "argv", "hermes": "argv"})
        claude = next(a for a in cfg["agents"] if a["name"] == "claude")
        self.assertEqual(council.build_argv(claude, "hello", None), ["claude", "-p", "--output-format", "stream-json",
                                                                   "--verbose", "--include-partial-messages"])
        self.assertEqual(cfg["settings"]["peer_turns"], "sequential")

    def test_failed_frame_falls_back_to_build_detection(self):
        self.assertTrue(council.detect_build("Build a CSV tool", "- [ ] Complete request"))
        self.assertFalse(council.detect_build("Build a CSV tool", "TASK_TYPE: question"))

    def test_consensus_requires_quorum_and_unambiguous_last_line(self):
        yes = {"ok": True, "answer": "Evidence\nCONSENSUS: yes"}
        self.assertTrue(council.has_consensus([yes, yes], 2))
        self.assertFalse(council.has_consensus([yes], 2))
        for answer in ("CONSENSUS: yes\nBut unresolved", "CONSENSUS: yes\nCONSENSUS: no", "No vote"):
            self.assertFalse(council.has_consensus([yes, {"ok": True, "answer": answer}], 2))


class OutputTests(TemporaryTest):
    def test_error_json_is_not_an_answer(self):
        for value in ('{"is_error": true, "result": "permission denied"}', '[]', '{}', 'not JSON'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                council.parse_output({"output_mode": "claude_json"}, value, None)

    def test_missing_file_does_not_use_diagnostic_stdout(self):
        with self.assertRaises(ValueError):
            council.parse_output({"output_mode": "file"}, "verbose logs", self.root / "missing")

    def test_cost_zero_and_invalid_costs(self):
        agent = {"output_mode": "claude_json"}
        self.assertEqual(council.extract_cost(agent, '{"total_cost_usd": 0}'), 0)
        for value in ("[]", "{}", '{"total_cost_usd": -1}', '{"total_cost_usd": "nan"}'):
            self.assertIsNone(council.extract_cost(agent, value))

    def test_timeout_preserves_partial_output(self):
        proc = council.run_process([sys.executable, "-c", "import time; print('partial', flush=True); time.sleep(20)"],
                                   cwd=self.root, timeout=0.3)
        self.assertIn("partial", proc["stdout"])
        self.assertIn("timed out", proc["error"])

    @unittest.skipUnless(os.name == "posix", "process group cleanup is POSIX-specific")
    def test_timeout_kills_descendants(self):
        child = "from pathlib import Path; import time; time.sleep(0.6); Path('escaped').touch()"
        parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1]]); time.sleep(20)"
        council.run_process([sys.executable, "-c", parent, child], cwd=self.root, timeout=0.25)
        time.sleep(0.7)
        self.assertFalse((self.root / "escaped").exists())

    def test_stdin_transport_has_no_argument_limit(self):
        # The reader sleeps past the first poll, so the remaining input is written on retries.
        script = "import sys, time; time.sleep(0.5); print(len(sys.stdin.read()))"
        proc = council.run_process([sys.executable, "-c", script], cwd=self.root, timeout=10,
                                   input_text="x" * 1_000_000)
        self.assertEqual((proc["returncode"], proc["stdout"].strip(), proc["error"]), (0, "1000000", ""))

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux per-argument limit")
    def test_oversize_argument_prompt_fails_before_launch(self):
        marker = self.root / "launched"
        agent = {"name": "big", "output_mode": "stdout", "timeout_sec": 5,
                 "argv": [sys.executable, "-c", f"open({str(marker)!r}, 'w')", "{prompt}"]}
        with redirect_stdout(io.StringIO()):
            result = council.run_agent(agent, "x" * 200_000, self.root, self.root, "round1")
            stdin_agent = dict(agent, prompt_via="stdin",
                               argv=[sys.executable, "-c", "import sys; print(len(sys.stdin.read()))"])
            via_stdin = council.run_agent(stdin_agent, "x" * 200_000, self.root, self.root, "round1")
        self.assertFalse(result["ok"])
        self.assertIn("limits one command-line argument", result["error"])
        self.assertFalse(marker.exists())
        self.assertEqual(via_stdin["answer"], "200000")


class ClaudeIsolationTests(TemporaryTest):
    def test_seed_copies_configuration_privately(self):
        for top_level in (True, False):
            with self.subTest(top_level_json=top_level):
                home = make_claude_home(self.root / f"home-{top_level}", top_level)
                target = self.root / f"seed-{top_level}" / "claude-config"
                with patch.dict(os.environ, {"HOME": str(home)}):
                    council.seed_claude_config(str(target))
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
                for name in (".credentials.json", "settings.json", ".claude.json"):
                    self.assertEqual(stat.S_IMODE((target / name).stat().st_mode), 0o600, name)
                self.assertEqual((target / ".credentials.json").read_text(), '{"token": "fixture"}')
                self.assertEqual((target / ".claude.json").read_text(),
                                 '{"top": true}' if top_level else '{"nested": true}')


class BuildTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.rounds = self.root / "rounds"
        self.rounds.mkdir()

    def verify(self, text, timeout=5):
        with redirect_stdout(io.StringIO()):
            return council.verify_build(text, self.workspace, self.rounds, timeout)

    def test_empty_file_and_trailing_newline(self):
        text = "FILE: __init__.py\n```python\n```\nFILE: x.py\n```python\nx = 1\n```"
        self.assertEqual(council.extract_files(text), [("__init__.py", ""), ("x.py", "x = 1\n")])

    def test_long_fence_allows_markdown_contents(self):
        text = "FILE: README.md\n````markdown\n```python\nprint(1)\n```\n````"
        self.assertIn("```python", council.extract_files(text)[0][1])

    def test_unclosed_fence_fails_without_writes(self):
        report = self.verify("FILE: a.py\n```python\nprint(1)\nRUN: true")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_run_inside_file_is_ignored(self):
        text = "FILE: x.txt\n```\nRUN: false\n```\nRUN: true"
        self.assertEqual(council.extract_run(text), "true")

    def test_multiple_run_commands_rejected(self):
        with self.assertRaises(ValueError):
            council.extract_run("RUN: true\nRUN: false")

    def test_invalid_paths_reject_whole_build(self):
        for path in ("../escape", "/tmp/escape", ".", "a/../../escape"):
            with self.subTest(path=path):
                report = self.verify(f"FILE: valid.txt\n```\nvalid\n```\nFILE: {path}\n```\nbad\n```\nRUN: true")
                self.assertEqual(report["status"], "failed")
                self.assertEqual(list(self.workspace.iterdir()), [])

    def test_symlink_escape_rejected(self):
        (self.workspace / "link").symlink_to(self.root, target_is_directory=True)
        report = self.verify("FILE: link/escape\n```\nbad\n```\nRUN: true")
        self.assertEqual(report["status"], "failed")
        self.assertFalse((self.root / "escape").exists())

    def test_duplicate_files_and_directory_conflicts(self):
        for second in ("a", "a/b"):
            report = self.verify(f"FILE: a\n```\none\n```\nFILE: {second}\n```\ntwo\n```\nRUN: true")
            self.assertEqual(report["status"], "failed")
            self.assertEqual(list(self.workspace.iterdir()), [])

    def test_missing_files_or_command_not_success(self):
        for text in ("done", "RUN: true", "FILE: a\n```\n```", "FILE: a\n```\n```\nRUN:"):
            self.assertEqual(self.verify(text)["status"], "failed")

    def test_real_test_command_passes_and_fails(self):
        for code, status in ((0, "passed"), (3, "failed")):
            text = f"FILE: test.py\n```python\nraise SystemExit({code})\n```\nRUN: {shlex.quote(sys.executable)} test.py"
            report = self.verify(text)
            self.assertEqual(report["status"], status)
            self.assertEqual(report["run_rc"], code)

    def test_build_timeout(self):
        text = f"FILE: test.py\n```python\nimport time; time.sleep(20)\n```\nRUN: {shlex.quote(sys.executable)} test.py"
        report = self.verify(text, timeout=0.2)
        self.assertEqual(report["status"], "failed")
        self.assertIn("timed out", report["error"])


class IntegrationTests(TemporaryTest):
    def invoke(self, scenario="question", extra=(), cfg=None, prompt="Explain this tradeoff"):
        config = self.write_config(cfg or self.config(scenario))
        args = [sys.executable, str(PROJECT_ROOT / "council.py"), "--config", str(config),
                "--sessions-dir", str(self.root / "sessions"), "--quiet", *extra, prompt]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=15)
        sessions = list((self.root / "sessions").glob("*/session.json"))
        meta = json.loads(sessions[-1].read_text()) if sessions else None
        return proc, meta, sessions[-1].parent if sessions else None

    def test_full_debate_three_adapters_and_cost(self):
        proc, meta, session = self.invoke()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(meta["consensus"])
        self.assertEqual(meta["rounds_run"], 3)
        self.assertEqual(meta["total_cost_usd_claude"], 1.25)
        self.assertEqual(len(meta["calls"]), 11)
        self.assertIn("ORIGINAL_EVIDENCE_alpha", (session / "rounds/final.alpha.prompt.txt").read_text())
        self.assertEqual((session / "agents/beta/owner.txt").read_text(), "beta")

    def test_full_debate_with_stdin_prompt_agent(self):
        cfg = self.config()
        cfg["agents"][0]["argv"][-1] = "-"
        cfg["agents"][0]["prompt_via"] = "stdin"
        proc, meta, session = self.invoke(cfg=cfg)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(meta["consensus"])
        self.assertEqual(meta["synthesizer"], "alpha")
        # The fixture asserts this evidence is in every later prompt it reads from stdin.
        self.assertIn("ORIGINAL_EVIDENCE_alpha", (session / "rounds/final.alpha.prompt.txt").read_text())

    def test_run_seeds_private_session_claude_config(self):
        # The shipped claude agent sets CLAUDE_CONFIG_DIR; seeding must work in a real run.
        cfg = self.config()
        cfg["agents"][0]["env"]["CLAUDE_CONFIG_DIR"] = "{session}/claude-config"
        with patch.dict(os.environ, {"HOME": str(make_claude_home(self.root / "home"))}):
            proc, meta, session = self.invoke(cfg=cfg)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        copied = session / "claude-config/.credentials.json"
        self.assertEqual(copied.read_text(), '{"token": "fixture"}')
        self.assertEqual(stat.S_IMODE(copied.stat().st_mode), 0o600)

    def test_unexpected_error_still_finishes_session(self):
        cfg = self.config()
        cfg["agents"][0]["env"]["CLAUDE_CONFIG_DIR"] = "{session}/claude-config"
        config = self.write_config(cfg)
        with patch.object(council, "seed_claude_config", side_effect=TypeError("simulated bug")), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            code = council.main(["--config", str(config), "--sessions-dir", str(self.root / "sessions"),
                                 "--non-interactive", "--quiet", "Explain"])
        self.assertEqual(code, 75)
        session = next((self.root / "sessions").iterdir())
        self.assertEqual(json.loads((session / "session.json").read_text())["status"], "paused")
        self.assertIn("TypeError: simulated bug", (session / "error.txt").read_text())
        self.assertIn("simulated bug", err.getvalue())
        self.assertTrue((session / "final.md").is_file())

    def test_drop_is_preserved_with_quorum(self):
        proc, meta, _ = self.invoke("drop")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("gamma", meta["dropped"])
        self.assertEqual(meta["drop_history"][0]["tag"], "round2")
        self.assertTrue(meta["consensus"])

    def test_quorum_loss_cannot_be_consensus(self):
        proc, meta, session = self.invoke("lose_quorum")
        self.assertEqual(proc.returncode, 75)
        self.assertEqual(meta["status"], "paused")
        self.assertFalse(meta["consensus"])
        self.assertTrue((session / "final.md").is_file())

    def test_all_failures_leave_session_summary(self):
        proc, meta, _ = self.invoke("fail_all")
        self.assertEqual(proc.returncode, 75)
        self.assertEqual(meta["status"], "paused")
        self.assertEqual(len(meta["drop_history"]), 3)

    def test_synthesis_fallback(self):
        proc, meta, _ = self.invoke("fallback")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(meta["synthesizer"], "beta")
        self.assertIn("alpha", meta["dropped"])

    def test_synthesis_failure_returns_failure(self):
        proc, meta, _ = self.invoke("fail_final")
        self.assertEqual(proc.returncode, 75)
        self.assertEqual(meta["status"], "paused")

    def test_failed_framing_does_not_force_question(self):
        proc, meta, session = self.invoke("fail_frame", prompt="Build a Python tool")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertTrue(meta["is_build"])
        self.assertEqual(meta["build"]["status"], "passed")
        self.assertEqual((session / "workspace/__init__.py").read_text(), "")

    def test_build_failure_returns_failure(self):
        proc, meta, _ = self.invoke("bad_build", extra=["--type", "build"])
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(meta["status"], "build_failed")
        self.assertEqual(meta["build"]["run_rc"], 3)

    def test_disabled_build_verification_is_unverified(self):
        proc, meta, session = self.invoke(extra=["--type", "build", "--no-verify"])
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(meta["status"], "build_unverified")
        self.assertEqual(list((session / "workspace").iterdir()), [])

    def test_round_limit_one_and_invalid_zero(self):
        proc, meta, _ = self.invoke(extra=["--max-rounds", "1"])
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(meta["rounds_run"], 1)
        self.assertFalse(meta["consensus"])
        proc, _, _ = self.invoke(extra=["--max-rounds", "0"])
        self.assertEqual(proc.returncode, 2)

    def test_missing_agents_preflight(self):
        cfg = self.config()
        for agent in cfg["agents"][1:]:
            agent["argv"][0] = "council-definitely-missing-cli"
        proc, meta, _ = self.invoke(cfg=cfg)
        self.assertEqual(proc.returncode, 75)
        self.assertEqual(meta["status"], "paused")
        self.assertEqual(len(meta["drop_history"]), 2)

    def test_doctor_does_not_call_agents(self):
        proc, meta, _ = self.invoke("fail_all", extra=["--doctor"])
        self.assertEqual(proc.returncode, 0)
        self.assertIsNone(meta)
        self.assertNotIn("fixture", proc.stderr)
        self.assertIn("prompt=argv", proc.stdout)
        self.assertIn("peer turns=sequential", proc.stdout)

    def test_soft_cap_before_round_one(self):
        cfg = self.config()
        cfg["settings"]["session_wallclock_cap_sec"] = 0.0001
        proc, meta, _ = self.invoke(cfg=cfg)
        self.assertEqual(proc.returncode, 75)
        self.assertEqual(meta["status"], "paused")

    def test_prompt_from_file_and_stdin(self):
        text = "# Request\n\nExplain this tradeoff for café owners.\n\n- Keep the answer concise.\n"
        prompt_file = self.root / "prompt.txt"
        prompt_file.write_text(text, encoding="utf-8")
        markdown_file = self.root / "my request.md"
        markdown_file.write_text(text, encoding="utf-8-sig")
        config = self.write_config(self.config())
        base = [sys.executable, str(PROJECT_ROOT / "council.py"), "--config", str(config),
                "--sessions-dir", str(self.root / "sessions"), "--max-rounds", "1", "--quiet"]
        for extra, input_text in ((["--prompt-file", str(prompt_file)], None),
                                  (["-f", str(markdown_file)], None), (["-"], text)):
            with self.subTest(extra=extra):
                proc = subprocess.run(base + extra, input=input_text, capture_output=True, text=True, timeout=10)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                session = max((self.root / "sessions").iterdir(), key=lambda p: p.stat().st_mtime_ns)
                snapshot = json.loads((session / "run.json").read_text())
                self.assertEqual(snapshot["options"]["prompt"], text)
                self.assertIn(text, (session / "rounds/round1.alpha.prompt.txt").read_text())

    def test_file_prompt_resume_uses_saved_content(self):
        prompt_file = self.root / "request.md"
        text = "# Request\n\nExplain this tradeoff.\n"
        prompt_file.write_text(text, encoding="utf-8")
        config = self.write_config(self.config())
        base = [sys.executable, str(PROJECT_ROOT / "council.py"), "--sessions-dir", str(self.root / "sessions"),
                "--non-interactive", "--quiet"]
        paused = subprocess.run(base + ["--config", str(config), "-f", str(prompt_file), "--max-cost", "0.1"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(paused.returncode, 75, paused.stdout + paused.stderr)
        prompt_file.unlink()
        session = next((self.root / "sessions").iterdir())
        resumed = subprocess.run(base + ["--resume", str(session), "--max-cost", "10"],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        self.assertIn(text, (session / "rounds/round1.alpha.prompt.txt").read_text())

    @unittest.skipUnless(os.name == "posix", "signal test")
    def test_interrupt_saves_partial_session(self):
        config = self.write_config(self.config("sleep"))
        proc = subprocess.Popen([sys.executable, str(PROJECT_ROOT / "council.py"), "--config", str(config),
                                 "--sessions-dir", str(self.root / "sessions"), "--quiet", "Explain"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not list((self.root / "sessions").glob("*/rounds/round0.alpha.prompt.txt")):
                if time.monotonic() > deadline:
                    self.fail("session did not start")
                time.sleep(0.02)
            proc.send_signal(signal.SIGINT)
            stdout, stderr = proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, 130, stdout + stderr)
            meta = json.loads(next((self.root / "sessions").glob("*/session.json")).read_text())
            self.assertEqual(meta["status"], "interrupted")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    def test_launcher_resolves_symlink_and_preserves_arguments(self):
        bindir = self.root / "bin"
        bindir.mkdir()
        uv = bindir / "uv"
        uv.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
        uv.chmod(0o755)
        link = bindir / "council"
        link.symlink_to(PROJECT_ROOT / "council")
        proc = subprocess.run([str(link), "spaces and 'quotes'"], env=os.environ | {"PATH": f"{bindir}:{os.environ['PATH']}"},
                              capture_output=True, text=True, check=True)
        self.assertIn(str(PROJECT_ROOT / "council.py"), proc.stdout.splitlines())
        self.assertEqual(proc.stdout.splitlines()[-1], "spaces and 'quotes'")


if __name__ == "__main__":
    unittest.main()
