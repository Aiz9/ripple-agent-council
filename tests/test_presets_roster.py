"""Presets and automatic roster selection."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import yaml

from agent_council import cli
from agent_council.roster import choose

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fake_agent.py").resolve()
ONLY_PYTHON = os.path.dirname(sys.executable)  # PATH on which no real agent CLI is installed


def agents(*roles):
    entries = []
    for (name, mode), role in zip((("alpha", "claude_json"), ("beta", "file"), ("gamma", "stdout")), roles):
        argv = [sys.executable, str(FIXTURE), name, mode, "{prompt}"] + (["{outfile}"] if mode == "file" else [])
        entries.append({"name": name, "output_mode": mode, "argv": argv, "role_preset": role,
                        "env": {"SCENARIO": "question"}})
    return entries


class PresetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_council(self, cfg, *args, env=None):
        config = self.root / "agents.yaml"
        config.write_text(yaml.safe_dump(cfg))
        proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--config", str(config),
                               "--sessions-dir", str(self.root / "s"), "--quiet", *args],
                              capture_output=True, text=True, timeout=30, env=env)
        sessions = sorted((self.root / "s").glob("*/session.json"), key=os.path.getmtime)
        return proc, json.loads(sessions[-1].read_text()) if sessions else None

    def base(self):
        return {"settings": {"max_rounds": 3, "per_agent_timeout_sec": 5},
                "agents": agents("architect", "implementer", "skeptic")}

    def test_listing(self):
        proc, _ = self.run_council(self.base() | {"presets": {"mine": {"description": "Mine.", "max_rounds": 1}}},
                                   "--presets")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for name in ("quick (built-in)", "deep (built-in)", "safe-build (built-in)", "mine (config): Mine."):
            self.assertIn(name, proc.stdout)

    def test_builtin_preset_and_explicit_override(self):
        proc, meta = self.run_council(self.base(), "--non-interactive", "--preset", "quick", "Explain")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual((meta["rounds_run"], meta["peer_turns"]), (2, "parallel"))
        proc, meta = self.run_council(self.base(), "--non-interactive", "--preset", "quick", "--max-rounds", "1",
                                      "--peer-turns", "sequential", "Explain")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual((meta["rounds_run"], meta["peer_turns"]), (1, "sequential"))

    def test_config_preset_with_roster_and_roles(self):
        cfg = self.base() | {"presets": {"pair": {"agents": ["alpha", "beta"], "anonymous_review": True,
                                                  "roles": {"alpha": "tester", "beta": "Check the numbers."}}}}
        proc, meta = self.run_council(cfg, "--non-interactive", "--preset", "pair", "Explain")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(meta["agents"], ["alpha", "beta"])
        self.assertEqual((meta["roles"]["alpha"]["name"], meta["roles"]["beta"]["description"]),
                         ("tester", "Check the numbers."))
        self.assertEqual(meta["peer_review"]["valid_ballots"], 2)

    def test_safe_build_preset_demands_a_terminal(self):
        proc, _ = self.run_council(self.base(), "--preset", "safe-build", "Build it")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("needs an interactive terminal", proc.stderr)

    def test_invalid_presets(self):
        proc, _ = self.run_council(self.base(), "--preset", "nope", "Explain")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown preset", proc.stderr)
        for bad in ({"x": {"colour": "red"}}, {"x": {"type": "poem"}}, ["x"]):
            with self.subTest(bad=bad), self.assertRaises(cli.ConfigError):
                cli.validate_config(self.base() | {"presets": bad})


class RosterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.sessions = Path(temporary.name) / "sessions"
        self.sessions.mkdir()
        self.cfg = cli.validate_config({"agents": agents("architect", "implementer", "researcher")})

    def block(self, name, kind, age_seconds):
        session = self.sessions / f"s-{name}-{age_seconds}"
        session.mkdir()
        meta = session / "session.json"
        meta.write_text(json.dumps({"unavailable": {name: kind}}))
        stamp = time.time() - age_seconds
        os.utime(meta, (stamp, stamp))

    def test_skips_recently_limited_agents(self):
        self.block("beta", "capacity", 600)
        self.block("gamma", "capacity", 5 * 3600)  # too old to matter
        names, notes = choose(self.cfg, sessions_root=self.sessions, is_build=False, path=ONLY_PYTHON)
        self.assertNotIn("beta", names)
        self.assertEqual(notes, ["skipped beta: hit an account or rate limit 10 min ago"])
        self.assertIn("gamma", names)

    def test_role_fit_orders_the_roster(self):
        self.assertEqual(choose(self.cfg, sessions_root=self.sessions, is_build=True, limit=2, path=ONLY_PYTHON)[0],
                         ["beta", "alpha"])     # implementer, then architect
        self.assertEqual(choose(self.cfg, sessions_root=self.sessions, is_build=False, limit=2, path=ONLY_PYTHON)[0],
                         ["gamma", "alpha"])    # researcher, then architect

    def test_refuses_to_go_below_quorum(self):
        self.block("alpha", "authentication", 60)
        self.block("beta", "capacity", 60)
        with self.assertRaisesRegex(ValueError, "found only 1 usable agent"):
            choose(self.cfg, sessions_root=self.sessions, is_build=False, path=ONLY_PYTHON)

    def test_auto_roster_end_to_end(self):
        root = self.sessions.parent
        config = root / "agents.yaml"
        config.write_text(yaml.safe_dump({"settings": {"max_rounds": 3, "per_agent_timeout_sec": 5},
                                          "agents": agents("architect", "implementer", "researcher")}))
        proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--config", str(config),
                               "--sessions-dir", str(self.sessions), "--non-interactive", "--quiet",
                               "--agents", "auto:2", "Explain the tradeoff"], capture_output=True, text=True,
                              timeout=30, env=os.environ | {"PATH": ONLY_PYTHON})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Auto roster: gamma, alpha", proc.stdout)


if __name__ == "__main__":
    unittest.main()
