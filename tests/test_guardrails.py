"""Guardrails as code: rule validation, checks, and enforcement during a run."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

from agent_council import cli
from agent_council.guardrails import check, phase_of, validate_rules

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fake_agent.py").resolve()


class RuleTests(unittest.TestCase):
    def rules(self, *raw):
        return validate_rules(list(raw))

    def test_each_rule(self):
        rules = self.rules({"rule": "forbid", "pattern": r"should work"},
                           {"rule": "require", "pattern": r"https?://", "message": "cites no source"},
                           {"rule": "max_chars", "value": 30, "action": "flag"},
                           {"rule": "require_evidence_tags"},
                           {"rule": "no_secrets"})
        found = check(rules, "It should work. AKIA" + "A" * 16, agent="a", tag="round1")
        messages = [(v["rule"], v["action"], v["message"]) for v in found]
        self.assertIn(("forbid", "reject", "contains forbidden text 'should work'"), messages)
        self.assertIn(("require", "reject", "cites no source"), messages)
        self.assertIn(("max_chars", "flag", "is 36 characters; the limit is 30"), messages)
        self.assertTrue(any(v["rule"] == "require_evidence_tags" for v in found))
        self.assertIn(("no_secrets", "reject", "appears to contain a AWS access key"), messages)
        self.assertEqual(check(rules, "[VERIFIED: ran] https://x.org", agent="a", tag="final"), [])

    def test_phase_and_agent_scoping(self):
        rules = self.rules({"rule": "forbid", "pattern": "x", "phases": ["final"], "agents": ["codex"]})
        self.assertEqual(check(rules, "x", agent="codex", tag="round2"), [])
        self.assertEqual(check(rules, "x", agent="claude", tag="final"), [])
        self.assertEqual(len(check(rules, "x", agent="codex", tag="final")), 1)
        self.assertEqual([phase_of(t) for t in ("round0", "plan2", "round3-revision", "ranking", "final")],
                         ["framing", "discussion", "discussion", "ranking", "final"])

    def test_python_validators_and_broken_ones(self):
        rules = self.rules({"python": "tests.guardrail_plugin:no_shouting"},
                           {"python": "tests.guardrail_plugin:broken", "action": "reject"})
        found = check(rules, "LOUD", agent="a", tag="round1")
        self.assertIn({"rule": "tests.guardrail_plugin:no_shouting", "action": "reject",
                       "message": "is written in capital letters only"}, found)
        broken = next(v for v in found if v["rule"].endswith("broken"))
        self.assertEqual(broken["action"], "flag")  # a crashing validator never rejects
        self.assertIn("validator bug", broken["message"])

    def test_invalid_rules(self):
        for raw in ({}, [5], [{"rule": "nope"}], [{"rule": "forbid"}], [{"rule": "forbid", "pattern": "("}],
                    [{"rule": "max_chars", "value": 0}], [{"rule": "no_secrets", "action": "block"}],
                    [{"rule": "no_secrets", "phases": ["later"]}], [{"python": "not a spec"}],
                    [{"rule": "no_secrets", "agents": "codex"}]):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_rules(raw)

    def test_config_validation_is_idempotent(self):
        cfg = {"agents": [{"name": "a", "argv": ["x", "{prompt}"]}, {"name": "b", "argv": ["y", "{prompt}"]}],
               "guardrails": [{"rule": "no_secrets"}]}
        once = cli.validate_config(cfg)
        self.assertEqual(cli.validate_config(once), once)
        with self.assertRaises(cli.ConfigError):
            cli.validate_config(cfg | {"guardrails": [{"rule": "nope"}]})


class EnforcementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_council(self, guardrails, scenario="question"):
        agents = []
        for name, mode in (("alpha", "claude_json"), ("beta", "file"), ("gamma", "stdout")):
            argv = [sys.executable, str(FIXTURE), name, mode, "{prompt}"] + (["{outfile}"] if mode == "file" else [])
            agents.append({"name": name, "output_mode": mode, "argv": argv, "env": {"SCENARIO": scenario}})
        config = self.root / "agents.yaml"
        config.write_text(yaml.safe_dump({"settings": {"max_rounds": 3, "per_agent_timeout_sec": 5},
                                          "agents": agents, "guardrails": guardrails}))
        proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--config", str(config),
                               "--sessions-dir", str(self.root / "s"), "--non-interactive", "--quiet", "Explain"],
                              cwd=PROJECT, capture_output=True, text=True, timeout=30)
        session = next((self.root / "s").iterdir())
        return proc, json.loads((session / "session.json").read_text()), session

    def test_flagged_answers_are_kept(self):
        proc, meta, session = self.run_council([{"rule": "forbid", "pattern": "CHALLENGE:", "action": "flag"}])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        events = meta["guardrail_events"]
        self.assertEqual({(e["agent"], e["tag"], e["action"]) for e in events},
                         {(n, "round1", "flag") for n in ("alpha", "beta", "gamma")})
        self.assertFalse(list((session / "rounds").glob("*-revision*")))
        self.assertIn("Guardrails: 0 rejected, 3 flagged", (session / "final.md").read_text())

    def test_rejected_answer_is_revised_once(self):
        proc, meta, session = self.run_council([{"rule": "forbid", "pattern": "CHALLENGE:", "phases": ["discussion"]}])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(meta["consensus"])
        self.assertEqual(sum(e["action"] == "reject" for e in meta["guardrail_events"]), 3)
        revision = (session / "rounds/round1-revision.alpha.prompt.txt").read_text()
        self.assertIn("GUARDRAIL FEEDBACK", revision)
        self.assertIn("Revised to comply", (session / "rounds/round2.alpha.prompt.txt").read_text())
        self.assertNotIn("CHALLENGE: retain", (session / "rounds/round2.alpha.prompt.txt").read_text())

    def test_failed_revision_drops_that_answer(self):
        proc, meta, _ = self.run_council([{"rule": "forbid", "pattern": "CHALLENGE:"}], scenario="stubborn")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("gamma", meta["dropped"])
        self.assertIn("guardrail rejected the revised answer", meta["dropped"]["gamma"])
        self.assertEqual(meta["rounds"][-1]["ok"], ["alpha", "beta"])


if __name__ == "__main__":
    unittest.main()
