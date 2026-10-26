"""Evaluation harness: council vs. single-agent baselines on a case file."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

from agent_council.evaluate import load_cases, parse_score, score_checks

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fake_agent.py").resolve()


class EvaluateUnitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, data):
        path = self.root / "cases.yaml"
        path.write_text(yaml.safe_dump(data))
        return path

    def test_load_normalizes_cases(self):
        cases = load_cases(self.write({"cases": [{"prompt": "Q", "expect": {"contains": "x"}}]}))
        self.assertEqual(cases[0]["id"], "case-1")
        self.assertEqual(cases[0]["type"], "auto")
        self.assertEqual(cases[0]["expect"]["contains"], ["x"])
        self.assertEqual(load_cases(self.write([{"id": "a", "prompt": "Q"}]))[0]["id"], "a")

    def test_load_rejects_bad_cases(self):
        for bad in ({"cases": []}, [{"prompt": ""}], [{"id": "a", "prompt": "Q"}, {"id": "a", "prompt": "R"}],
                    [{"prompt": "Q", "expect": {"smells": "nice"}}], [{"prompt": "Q", "expect": {"regex": "("}}],
                    [{"prompt": "Q", "type": "poem"}], [{"prompt": "Q", "judge": 3}]):
            with self.subTest(bad=bad), self.assertRaises(Exception):
                load_cases(self.write(bad))

    def test_checks(self):
        expect = {"contains": ["Postgres"], "not_contains": ["MongoDB"], "regex": [r"\bACID\b"], "build_passes": True}
        checks = score_checks(expect, "postgres gives ACID", "passed")
        self.assertTrue(all(c["ok"] for c in checks))
        checks = score_checks(expect, "MongoDB", "failed")
        self.assertEqual([c["ok"] for c in checks], [False, False, False, False])

    def test_parse_score(self):
        self.assertEqual(parse_score("Good.\nSCORE: 7"), 7.0)
        self.assertEqual(parse_score("SCORE: 4\nrevised\nscore: 9.5/10"), 9.5)
        self.assertEqual(parse_score("SCORE: 15"), 10.0)
        self.assertIsNone(parse_score("I give it a seven"))


class EvaluateEndToEndTests(unittest.TestCase):
    def test_council_against_baseline_with_judge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agents = []
            for name, mode in (("alpha", "claude_json"), ("beta", "file"), ("gamma", "stdout")):
                argv = [sys.executable, str(FIXTURE), name, mode, "{prompt}"] + (["{outfile}"] if mode == "file" else [])
                agents.append({"name": name, "output_mode": mode, "argv": argv, "env": {"SCENARIO": "question"}})
            config = root / "agents.yaml"
            config.write_text(yaml.safe_dump({"settings": {"max_rounds": 3, "per_agent_timeout_sec": 5},
                                              "agents": agents}))
            cases = root / "cases.yaml"
            cases.write_text(yaml.safe_dump({"cases": [
                {"id": "db", "prompt": "Which database?", "type": "question",
                 "expect": {"contains": ["original evidence"]}, "judge": "Cites evidence."},
                {"id": "build", "prompt": "Build a calculator", "type": "build", "expect": {"build_passes": True}},
            ]}))
            out = root / "eval"
            proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--config", str(config),
                                   "--sessions-dir", str(root / "s"), "--agents", "alpha,beta", "--eval", str(cases),
                                   "--baseline", "gamma", "--judge", "alpha", "--eval-dir", str(out)],
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            results = json.loads((out / "results.json").read_text())
            summary = results["summary"]
            self.assertEqual((summary["council"]["passed"], summary["council"]["mean_judge_score"]), (2, 8.0))
            self.assertEqual((summary["baseline:gamma"]["passed"], summary["baseline:gamma"]["mean_judge_score"]),
                             (1, 3.0))  # its answer lacks the evidence, but its build passes
            db = results["cases"][0]["results"]
            self.assertEqual(json.loads(Path(db[0]["session"], "session.json").read_text())["agents"],
                             ["alpha", "beta"])
            self.assertEqual(results["cases"][1]["results"][1]["build"], "passed")
            self.assertIn("| council | 2/2 | 100% | 8.0 |", (out / "report.md").read_text())
            self.assertIn("baseline:gamma", proc.stdout)
            self.assertFalse(list(out.rglob("claude-config")))

    def test_eval_options_need_eval(self):
        proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--baseline", "claude", "Q"],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("require --eval", proc.stderr)


if __name__ == "__main__":
    unittest.main()
