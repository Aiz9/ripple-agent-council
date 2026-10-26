"""Usage normalization, session budgets, and resuming after a budget pause."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

from agent_council.usage import budget_exceeded, describe, parse_usage, summarize

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fake_agent.py").resolve()
CLAUDE = {"output_mode": "claude_json"}


class ParseTests(unittest.TestCase):
    def test_claude_envelope_sums_cached_input(self):
        stdout = json.dumps({"result": "x", "total_cost_usd": 0.19, "usage": {
            "input_tokens": 6, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 30}})
        self.assertEqual(parse_usage(CLAUDE, stdout, ""),
                         {"input_tokens": 156, "output_tokens": 30, "total_tokens": 186, "cost_usd": 0.19})

    def test_codex_stderr_tokens(self):
        stderr = "transcript...\ntokens used\n26,000\n"
        self.assertEqual(parse_usage({"output_mode": "file"}, "", stderr)["total_tokens"], 26000)

    def test_unreported_or_malformed_usage_is_none(self):
        for agent, stdout, stderr in ((CLAUDE, "not json", ""), (CLAUDE, json.dumps({"result": "x"}), ""),
                                      ({"output_mode": "stdout"}, "tokens used\n5", ""),  # stdout text is an answer
                                      ({"output_mode": "stdout"}, "plain answer", "")):
            with self.subTest(stdout=stdout):
                self.assertIsNone(parse_usage(agent, stdout, stderr))

    def test_negative_or_nonnumeric_values_are_ignored(self):
        stdout = json.dumps({"total_cost_usd": -1, "usage": {"input_tokens": -5, "output_tokens": "9"}})
        self.assertEqual(parse_usage(CLAUDE, stdout, ""),
                         {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": None})


class BudgetTests(unittest.TestCase):
    results = [{"name": "claude", "tag": "round1", "usage": {"total_tokens": 150, "cost_usd": 0.25}},
               {"name": "codex", "tag": "round1", "usage": {"total_tokens": 1000, "cost_usd": None}},
               {"name": "hermes", "tag": "round1", "usage": None},
               {"name": "agy", "tag": "preflight", "usage": None}]

    def test_summary_and_unmetered_agents(self):
        summary = summarize(self.results)
        self.assertEqual((summary["total_tokens"], summary["total_cost_usd"], summary["unmetered_agents"]),
                         (1150, 0.25, ["hermes"]))
        self.assertIn("no usage reported by hermes", describe(summary))

    def test_caps(self):
        summary = summarize(self.results)
        self.assertIsNone(budget_exceeded(summary, {"max_cost_usd": None, "max_tokens": None}))
        self.assertIn("cost budget", budget_exceeded(summary, {"max_cost_usd": 0.25, "max_tokens": None}))
        self.assertIn("token budget", budget_exceeded(summary, {"max_cost_usd": 1, "max_tokens": 1000}))
        self.assertIsNone(budget_exceeded(summary, {"max_cost_usd": 1, "max_tokens": 5000}))


class BudgetRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        agents = []
        for name, mode in (("alpha", "claude_json"), ("beta", "file"), ("gamma", "stdout")):
            argv = [sys.executable, str(FIXTURE), name, mode, "{prompt}"] + (["{outfile}"] if mode == "file" else [])
            agents.append({"name": name, "output_mode": mode, "argv": argv, "env": {"SCENARIO": "question"}})
        self.config = self.root / "agents.yaml"
        self.config.write_text(yaml.safe_dump({"settings": {"max_rounds": 3, "per_agent_timeout_sec": 5}, "agents": agents}))

    def council(self, *args):
        return subprocess.run([sys.executable, str(PROJECT / "council.py"), "--sessions-dir", str(self.root / "s"),
                               "--quiet", *args], capture_output=True, text=True, timeout=30)

    def test_cost_budget_pauses_then_resume_with_more_budget_finishes(self):
        paused = self.council("--config", str(self.config), "--non-interactive", "--max-cost", "0.5", "Explain")
        self.assertEqual(paused.returncode, 75, paused.stdout + paused.stderr)
        session = next((self.root / "s").iterdir())
        meta = json.loads((session / "session.json").read_text())
        self.assertEqual((meta["status"], meta["pause_reason"]), ("paused", "budget_exhausted"))
        self.assertEqual(meta["usage"]["total_cost_usd"], 0.5)
        self.assertEqual(meta["usage"]["by_agent"]["beta"]["total_tokens"], 1000)  # Codex-style stderr
        self.assertEqual(meta["usage"]["unmetered_agents"], ["gamma"])
        self.assertIn("raise the budget", (session / "final.md").read_text())

        resumed = self.council("--resume", str(session), "--max-cost", "10")
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        meta = json.loads((session / "session.json").read_text())
        self.assertEqual(meta["status"], "completed")
        self.assertTrue(meta["consensus"])
        self.assertGreaterEqual(meta["reused_calls"], 4)  # framing + three round-1 answers
        self.assertEqual(json.loads((session / "run.json").read_text())["config"]["settings"]["max_cost_usd"], 10)

    def test_invalid_budget_rejected(self):
        for flag, value in (("--max-cost", "0"), ("--max-tokens", "-3")):
            with self.subTest(flag=flag):
                proc = self.council("--config", str(self.config), "--non-interactive", flag, value, "Explain")
                self.assertEqual(proc.returncode, 2, proc.stderr)


if __name__ == "__main__":
    unittest.main()
