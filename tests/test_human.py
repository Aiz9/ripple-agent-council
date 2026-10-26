"""Human checkpoints: guidance, checklist edits, early synthesis, stop/resume, and build approval."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import yaml

from agent_council import cli
from agent_council.human import parse_points

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fake_agent.py").resolve()


class HumanCheckpointTests(unittest.TestCase):
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

    def main(self, answers, *args):
        """Run cli.main as if in a terminal; return (exit code, number of answers used)."""
        # Redirect first: patching isatty on the original stderr would not survive the swap.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), \
                patch.object(sys.stdin, "isatty", return_value=True), patch.object(sys.stderr, "isatty", return_value=True), \
                patch("builtins.input", side_effect=answers) as ask:
            code = cli.main(["--sessions-dir", str(self.root / "s"), "--quiet", *args])
        return code, ask.call_count

    def start(self, answers, *args):
        return self.main(answers, "--config", str(self.config), "--mode", "peer-to-peer", *args, "Explain")

    def session(self):
        session = next((self.root / "s").iterdir())
        return session, json.loads((session / "session.json").read_text())

    def test_checklist_edit_and_guidance_reach_later_prompts(self):
        answers = ["e", "Cite two sources", "",          # framing: replace the definition of done
                   "g", "Prefer standard-library code", "",  # after round 1: guidance
                   ""]                                    # after round 2: continue
        code, used = self.start(answers, "--human-review", "framing,rounds")
        self.assertEqual((code, used), (0, 7))
        session, meta = self.session()
        self.assertEqual(meta["checklist"], ["Cite two sources"])
        round1 = (session / "rounds/round1.alpha.prompt.txt").read_text()
        self.assertIn("- [ ] Cite two sources", round1)
        self.assertNotIn("HUMAN GUIDANCE", round1)
        round2 = (session / "rounds/round2.beta.prompt.txt").read_text()
        self.assertIn("HUMAN GUIDANCE", round2)
        self.assertIn("- Prefer standard-library code", round2)
        self.assertEqual([d["point"] for d in meta["human_decisions"]], ["framing", "round1", "round2"])

    def test_synthesize_now_ends_the_debate(self):
        code, used = self.start(["s"], "--human-review", "rounds")
        self.assertEqual((code, used), (0, 1))
        _, meta = self.session()
        self.assertEqual((meta["rounds_run"], meta["debate_ended_by_user_after_round"]), (1, 1))
        self.assertEqual(meta["status"], "completed")

    def test_stop_then_resume_replays_decisions(self):
        code, _ = self.start(["g", "Be brief", "", "q"], "--human-review", "framing,rounds")
        self.assertEqual(code, 75)
        session, meta = self.session()
        self.assertEqual((meta["status"], meta["pause_reason"]), ("paused", "stopped_by_user"))
        # Framing guidance replays without asking; only rounds 1 and 2 ask again.
        code, used = self.main(["", ""], "--resume", str(session))
        self.assertEqual((code, used), (0, 2))
        meta = json.loads((session / "session.json").read_text())
        self.assertEqual(meta["status"], "completed")
        self.assertIn("- Be brief", (session / "rounds/final.alpha.prompt.txt").read_text())

    def test_resume_without_a_terminal_waits_for_the_human(self):
        self.start(["q"], "--human-review", "rounds")
        session, _ = self.session()
        proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--sessions-dir", str(self.root / "s"),
                               "--quiet", "--resume", str(session)], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 75, proc.stdout + proc.stderr)
        meta = json.loads((session / "session.json").read_text())
        self.assertEqual(meta["pause_reason"], "awaiting_human")

    def test_closed_stdin_at_a_checkpoint_pauses(self):
        code, _ = self.start(EOFError(), "--human-review", "framing")
        self.assertEqual(code, 75)
        _, meta = self.session()
        self.assertEqual(meta["pause_reason"], "awaiting_human")

    def test_build_command_needs_approval(self):
        code, _ = self.start(["n"], "--type", "build", "--human-review", "build")
        self.assertEqual(code, 1)
        session, meta = self.session()
        self.assertEqual(meta["build"]["error"], "declined at the human build checkpoint")
        self.assertEqual(list((session / "workspace").iterdir()), [])  # nothing written or run

    def test_approved_build_runs(self):
        code, used = self.start(["y"], "--type", "build", "--human-review", "build")
        self.assertEqual((code, used), (0, 1))
        _, meta = self.session()
        self.assertEqual(meta["build"]["status"], "passed")

    def test_requires_a_terminal(self):
        proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--config", str(self.config),
                               "--sessions-dir", str(self.root / "s"), "--human-review", "all", "Explain"],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("needs an interactive terminal", proc.stderr)

    def test_point_parsing(self):
        self.assertEqual(parse_points("all"), ["framing", "rounds", "synthesis", "build"])
        self.assertEqual(parse_points("build, framing"), ["framing", "build"])
        self.assertEqual(parse_points(None), [])
        for bad in ("later", ["framing", 3], 5):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_points(bad)


if __name__ == "__main__":
    unittest.main()
