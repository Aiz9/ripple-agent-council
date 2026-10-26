"""`council --add-agent`: the interactive wizard, its validation, saving, and test request."""
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import yaml

from agent_council import cli

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fake_agent.py").resolve()


class AddAgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "agents.yaml"
        agents = [{"name": name, "output_mode": "stdout", "argv": [sys.executable, str(FIXTURE), name, "stdout", "{prompt}"]}
                  for name in ("alpha", "beta")]
        self.path.write_text(yaml.safe_dump({"settings": {"per_agent_timeout_sec": 5}, "agents": agents}))

    def run_wizard(self, answers, probe_result=None):
        """Run --add-agent with scripted answers; return (exit code, probe mock, output)."""
        probe = patch.object(cli, "probe_agent", return_value=probe_result or {"ok": True, "seconds": 0.1, "answer": "READY"})
        with patch.object(sys.stdin, "isatty", return_value=True), patch.object(sys.stderr, "isatty", return_value=True), \
                patch("builtins.input", side_effect=answers), probe as mocked, redirect_stdout(io.StringIO()) as out:
            code = cli.main(["--add-agent", "--config", str(self.path)])
        return code, mocked, out.getvalue()

    def agent(self, name):
        return next(a for a in cli.load_config(self.path)["agents"] if a["name"] == name)

    def test_custom_command_with_prompt_argument(self):
        # adapter, name, command, output, role, every run?, test?
        code, probe, out = self.run_wizard(["", "llama", "ollama run llama3.2 {prompt}", "", "", "", "n"])
        self.assertEqual(code, 0, out)
        llama = self.agent("llama")
        self.assertEqual(llama["argv"], ["ollama", "run", "llama3.2", "{prompt}"])
        self.assertEqual((llama["prompt_via"], llama["output_mode"], llama["role_preset"], llama["enabled"]),
                         ("argv", "stdout", "reviewer", True))
        self.assertEqual([a["name"] for a in cli.load_config(self.path)["agents"]], ["alpha", "beta", "llama"])
        self.assertEqual(len(list(self.root.glob("agents.yaml.backup-*"))), 1)
        probe.assert_not_called()

    def test_command_without_prompt_uses_stdin_and_file_output(self):
        code, _, out = self.run_wizard(["", "mine", "mycli --model {model} --out {outfile}", "", "tester", "gpt-x", "n", "n"])
        self.assertEqual(code, 0, out)
        mine = self.agent("mine")
        self.assertEqual((mine["prompt_via"], mine["output_mode"], mine["model"], mine["enabled"]),
                         ("stdin", "file", "gpt-x", False))
        self.assertIn("sent on standard input", out)

    def test_builtin_adapter(self):
        code, _, out = self.run_wizard(["goose", "", "", "llama3", "", "n"])
        self.assertEqual(code, 0, out)
        goose = self.agent("goose")
        self.assertEqual((goose["adapter"], goose["model"], goose["role_preset"]), ("goose", "llama3", "researcher"))
        self.assertEqual(goose["env"]["GOOSE_MODE"], "chat")

    def test_invalid_answers_are_asked_again(self):
        answers = ["", "alpha", "new-one",               # duplicate name, then a new one
                   "tool --p={prompt}", "tool a | b",      # embedded placeholder, then a shell pipe
                   "tool {prompt}", "", "custom", "", "Check every claim.", "", "n"]
        code, _, out = self.run_wizard(answers)
        self.assertEqual(code, 0, out)
        self.assertIn("already exists", out)
        self.assertIn("as a separate word", out)
        self.assertIn("without a shell", out)
        agent = self.agent("new-one")
        self.assertEqual((agent["argv"], agent["role"]), (["tool", "{prompt}"], "Check every claim."))

    def test_failed_test_can_discard_the_agent(self):
        original = self.path.read_bytes()
        failure = {"ok": False, "seconds": 0.2, "answer": "", "error": "command not found: ollama"}
        code, probe, out = self.run_wizard(["", "llama", "ollama run llama3.2 {prompt}", "", "", "", "y", "n"], failure)
        self.assertEqual(code, 1)
        self.assertIn("command not found: ollama", out)
        self.assertEqual(self.path.read_bytes(), original)
        tested = probe.call_args.args[0]
        self.assertEqual((tested["name"], tested["timeout_sec"]), ("llama", 5))

    def test_passed_test_saves(self):
        code, probe, out = self.run_wizard(["", "llama", "ollama run llama3.2 {prompt}", "", "", "", "y"])
        self.assertEqual(code, 0, out)
        self.assertIn("Test passed", out)
        probe.assert_called_once()
        self.agent("llama")

    def test_requires_a_terminal(self):
        process = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--add-agent", "--config", str(self.path)],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 2)
        self.assertIn("requires an interactive terminal", process.stderr)

    def test_probe_runs_the_real_command(self):
        for transport, last in (("argv", "{prompt}"), ("stdin", "-")):
            with self.subTest(transport=transport):
                agent = cli.validate_config({"agents": [
                    {"name": "solo", "prompt_via": transport, "timeout_sec": 5,
                     "argv": [sys.executable, str(FIXTURE), "solo", "stdout", last]},
                    {"name": "other", "argv": ["true", "{prompt}"]}]})["agents"][0]
                before = cli.SHOW_DISCUSSION
                with redirect_stdout(io.StringIO()):
                    result = cli.probe_agent(agent)
                self.assertTrue(result["ok"], result["error"])
                self.assertEqual(cli.SHOW_DISCUSSION, before)  # restored after the probe


if __name__ == "__main__":
    unittest.main()
