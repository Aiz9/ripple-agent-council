"""Follow-up questions: conversation chains across sessions."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

from agent_council.followup import compose

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fake_agent.py").resolve()


class FollowUpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        agents = []
        for name, mode in (("alpha", "claude_json"), ("beta", "file"), ("gamma", "stdout")):
            argv = [sys.executable, str(FIXTURE), name, mode, "{prompt}"] + (["{outfile}"] if mode == "file" else [])
            agents.append({"name": name, "output_mode": mode, "argv": argv, "env": {"SCENARIO": "question"}})
        self.config = self.root / "agents.yaml"
        self.config.write_text(yaml.safe_dump({"settings": {"max_rounds": 3, "per_agent_timeout_sec": 5},
                                               "agents": agents}))

    def council(self, *args, env=None):
        import os
        environment = {k: v for k, v in os.environ.items() if k != "COUNCIL_CONFIG"} | (env or {})
        return subprocess.run([sys.executable, str(PROJECT / "council.py"), "--sessions-dir", str(self.root / "s"),
                               "--non-interactive", "--quiet", *args], capture_output=True, text=True, timeout=30,
                              env=environment)

    def newest(self):
        return max((self.root / "s").iterdir(), key=lambda s: s.stat().st_mtime_ns)

    def test_chain_of_follow_ups_inherits_the_council(self):
        first = self.council("--config", str(self.config), "First question?")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        a = self.newest()
        # No --config: the follow-up must reuse session A's council, not the bundled defaults.
        prompt_file = self.root / "follow-up.md"
        prompt_file.write_text("Second question?", encoding="utf-8")
        second = self.council("--continue", "--prompt-file", str(prompt_file))
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        b = self.newest()
        meta_b = json.loads((b / "session.json").read_text())
        self.assertEqual((meta_b["parent_session"], meta_b["question"]), (a.name, "Second question?"))
        self.assertEqual(meta_b["agents"], ["alpha", "beta", "gamma"])
        prompt_b = (b / "rounds/round1.alpha.prompt.txt").read_text()
        self.assertIn("PRIOR CONVERSATION", prompt_b)
        self.assertIn("First question?", prompt_b)
        self.assertIn(json.loads((a / "final-answer.json").read_text())["text"], prompt_b)
        self.assertIn("NEW QUESTION:\nSecond question?", prompt_b)

        third = self.council("--continue-from", b.name, "Third question?")
        self.assertEqual(third.returncode, 0, third.stdout + third.stderr)
        c = self.newest()
        prompt_c = (c / "rounds/round1.alpha.prompt.txt").read_text()
        self.assertLess(prompt_c.index("First question?"), prompt_c.index("Second question?"))
        self.assertIn(f"Continues session: {b.name}", (c / "final.md").read_text())
        listing = self.council("--sessions").stdout
        self.assertIn(f"Third question?  (continues {b.name})", listing)

    def test_unfinished_or_missing_parent_rejected(self):
        for args in (("--continue",), ("--continue-from", "no-such-session")):
            with self.subTest(args=args):
                proc = self.council(*args, "Q?")
                self.assertEqual(proc.returncode, 2)
                self.assertIn("council:", proc.stderr)
        proc = self.council("--resume", "latest", "--continue")
        self.assertEqual(proc.returncode, 2)
        proc = self.council("--continue", "--continue-from", "x", "Q?")
        self.assertEqual(proc.returncode, 2)

    def test_follow_up_resumes_with_its_context(self):
        self.council("--config", str(self.config), "First question?")
        paused = self.council("--continue", "--max-cost", "0.5", "Second question?")
        self.assertEqual(paused.returncode, 75, paused.stdout + paused.stderr)
        b = self.newest()
        resumed = self.council("--resume", str(b), "--max-cost", "10")
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        meta = json.loads((b / "session.json").read_text())
        self.assertEqual((meta["status"], meta["question"]), ("completed", "Second question?"))
        self.assertIsNotNone(meta["parent_session"])

    def test_compose_format(self):
        text = compose([{"session": "s1", "question": "Q1", "answer": "A1"}], "Q2")
        self.assertTrue(text.index("Q1") < text.index("A1") < text.index("NEW QUESTION:\nQ2"))


if __name__ == "__main__":
    unittest.main()
