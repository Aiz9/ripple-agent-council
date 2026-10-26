"""Anonymous peer ranking: labels, scrubbing, ballots, Borda count, and a full run."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

from agent_council.review import INSTRUCTION, assign_labels, parse_ballot, scrub, synthesis_note, tally

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fake_agent.py").resolve()


class RankingUnitTests(unittest.TestCase):
    def test_labels_are_session_stable(self):
        names = ["claude", "codex", "agy", "hermes"]
        self.assertEqual(assign_labels(names, "session-1"), assign_labels(list(reversed(names)), "session-1"))
        self.assertEqual(sorted(assign_labels(names, "s").values()), sorted(names))
        self.assertEqual(list(assign_labels(names, "s")), ["A", "B", "C", "D"])

    def test_scrub_removes_whole_names_only(self):
        text = "Claude agrees with codex, unlike claude-2; the alphabet and codexes stay."
        self.assertEqual(scrub(text, ["claude", "codex", "alpha"]),
                         "[peer] agrees with [peer], unlike claude-2; the alphabet and codexes stay.")

    def test_ballot_parsing(self):
        labels = ["A", "B", "C"]
        self.assertEqual(parse_ballot("Reasons.\nRANKING: Response B > **a** > c", labels), ["B", "A", "C"])
        self.assertEqual(parse_ballot("RANKING: A > B > C\nRANKING: C > B > A", labels), ["C", "B", "A"])
        for text in ("no ballot", "RANKING: A > B", "RANKING: A > A > B", "RANKING: A > B > C > D"):
            with self.subTest(text=text):
                self.assertIsNone(parse_ballot(text, labels))

    def test_borda_count_and_ties(self):
        labels = {"A": "alpha", "B": "beta", "C": "gamma"}
        result = tally({"x": ["A", "B", "C"], "y": ["B", "A", "C"], "z": None}, labels)
        self.assertEqual(result["scores"], {"alpha": 3, "beta": 3, "gamma": 0})
        self.assertEqual((result["ranking"], result["valid_ballots"]), (["alpha", "beta", "gamma"], 2))
        self.assertIn("1. alpha (3 points)", synthesis_note(result))
        self.assertIn("inconclusive", synthesis_note(tally({"x": ["A", "B", "C"]}, labels)))


class RankingRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_council(self, scenario="question", *extra):
        agents = []
        for name, mode in (("alpha", "claude_json"), ("beta", "file"), ("gamma", "stdout")):
            argv = [sys.executable, str(FIXTURE), name, mode, "{prompt}"] + (["{outfile}"] if mode == "file" else [])
            agents.append({"name": name, "output_mode": mode, "argv": argv, "env": {"SCENARIO": scenario}})
        config = self.root / "agents.yaml"
        config.write_text(yaml.safe_dump({"settings": {"max_rounds": 3, "per_agent_timeout_sec": 5}, "agents": agents}))
        proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--config", str(config),
                               "--sessions-dir", str(self.root / "s"), "--non-interactive", "--quiet",
                               "--anonymous-review", *extra, "Explain"], capture_output=True, text=True, timeout=30)
        session = next((self.root / "s").iterdir())
        return proc, json.loads((session / "session.json").read_text()), session

    def test_blind_ranking_reaches_synthesis(self):
        proc, meta, session = self.run_council("named")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        review = meta["peer_review"]
        self.assertEqual(review["valid_ballots"], 3)
        self.assertEqual(sorted(review["ranking"]), ["alpha", "beta", "gamma"])
        self.assertEqual(sum(review["scores"].values()), 3 * 3)  # 3 ballots x (2+1+0)
        ballot_prompt = (session / "rounds/ranking.beta.prompt.txt").read_text()
        responses = ballot_prompt.split(INSTRUCTION, 1)[1]
        for name in ("alpha", "beta", "gamma"):
            self.assertNotIn(name, responses.lower())   # names scrubbed from the ballot section
        self.assertIn("I am [peer], and [peer] agrees.", responses)
        self.assertIn("ANONYMOUS PEER RANKING", (session / "rounds/final.alpha.prompt.txt").read_text())
        self.assertIn("Anonymous peer ranking:", (session / "final.md").read_text())

    def test_invalid_ballot_is_ignored_not_guessed(self):
        proc, meta, _ = self.run_council("bad_ballot")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(meta["peer_review"]["valid_ballots"], 2)
        self.assertIsNone(meta["peer_review"]["ballots"]["gamma"])

    def test_off_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            agents = [{"name": n, "argv": [sys.executable, str(FIXTURE), n, "stdout", "{prompt}"]} for n in ("alpha", "beta")]
            config = Path(directory) / "agents.yaml"
            config.write_text(yaml.safe_dump({"settings": {"max_rounds": 1}, "agents": agents}))
            proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--config", str(config),
                                   "--sessions-dir", str(Path(directory) / "s"), "--non-interactive", "--quiet", "Q"],
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            meta = json.loads(next((Path(directory) / "s").glob("*/session.json")).read_text())
            self.assertNotIn("peer_review", meta)


if __name__ == "__main__":
    unittest.main()
