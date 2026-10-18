"""Built-in adapter catalog, roster selection, and end-to-end output wiring."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import yaml

from agent_council import cli
from agent_council.adapters import ADAPTERS, select_agents

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fake_agent.py").resolve()
BYPASS_FLAGS = {"--yolo", "-y", "--dangerously-skip-permissions", "--allow-all-tools", "--allow-all",
                "--trust-all-tools", "--force", "-f", "--auto", "--yes-always", "--approval-mode=yolo"}


def without_model(argv):
    """argv as built with an empty model: the {model} token and its flag removed."""
    result = []
    for token in argv:
        if token == "{model}":
            result.pop()
        else:
            result.append(token)
    return result


class CatalogTests(unittest.TestCase):
    def test_every_adapter_is_a_valid_agent_without_permission_bypass(self):
        cfg = cli.validate_config({"agents": [{"adapter": name} for name in ADAPTERS]})
        self.assertEqual([a["name"] for a in cfg["agents"]], list(ADAPTERS))
        for agent in cfg["agents"]:
            with self.subTest(agent=agent["name"]):
                self.assertIn("{model}", agent["argv"])     # a model override is always possible
                self.assertEqual(agent["model"], "")         # ...but none is imposed
                self.assertNotIn("{model}", cli.build_argv(agent, "hi", Path("out")))
                self.assertFalse(BYPASS_FLAGS & set(agent["argv"]), "adapters must not bypass permissions")
                self.assertTrue(ADAPTERS[agent["name"]].source, "every adapter records its evidence")
        goose = next(a for a in cfg["agents"] if a["name"] == "goose")
        self.assertEqual(goose["env"]["GOOSE_MODE"], "chat")

    def test_default_agents_match_the_catalog(self):
        for agent in cli.load_config(cli.ROOT / "agents.yaml")["agents"]:
            with self.subTest(agent=agent["name"]):
                template = ADAPTERS[agent["name"]]
                self.assertEqual(agent["argv"], without_model(template.argv))
                self.assertEqual((agent["output_mode"], agent["prompt_via"]),
                                 (template.output_mode, template.prompt_via))

    def test_adapter_entries_keep_explicit_overrides(self):
        entry = {"adapter": "goose", "name": "goose-local", "model": "llama3", "timeout_sec": 60,
                 "env": {"GOOSE_PROVIDER": "ollama"}, "role": "Review numerical claims."}
        original = copy.deepcopy(entry)
        goose = cli.validate_config({"agents": [entry, {"adapter": "claude"}]})["agents"][0]
        self.assertEqual(goose["name"], "goose-local")
        self.assertEqual(goose["env"], {"GOOSE_MODE": "chat", "GOOSE_DISABLE_SESSION_NAMING": "true",
                                        "GOOSE_PROVIDER": "ollama"})
        self.assertEqual(cli.build_argv(goose, "hi", None)[-4:], ["--model", "llama3", "-i", "-"])
        self.assertNotIn("role_preset", goose)
        self.assertEqual(goose["timeout_sec"], 60)
        self.assertEqual(entry, original)

    def test_unknown_adapter_is_a_configuration_error(self):
        with self.assertRaises(cli.ConfigError):
            cli.validate_config({"agents": [{"adapter": "nope"}, {"adapter": "claude"}]})

    def test_validation_is_idempotent(self):
        once = cli.validate_config({"agents": [{"adapter": "aider"}, {"adapter": "opencode"}]})
        self.assertEqual(cli.validate_config(copy.deepcopy(once)), once)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = cli.load_config(cli.ROOT / "agents.yaml")

    def test_roster_mixes_configured_agents_and_adapters(self):
        cfg = cli.validate_config(select_agents(self.cfg, "hermes, gemini,claude"))
        self.assertEqual([a["name"] for a in cfg["agents"]], ["hermes", "gemini", "claude"])
        self.assertEqual(cfg["settings"]["synthesizer"], "claude")
        self.assertEqual(cfg["agents"][0]["argv"], ["hermes", "-z", "{prompt}"])  # configured, not template
        self.assertEqual(len(self.cfg["agents"]), 4)

    def test_missing_synthesizer_prefers_a_stdin_agent(self):
        cfg = select_agents(self.cfg, "agy,opencode,gemini")
        self.assertEqual(cfg["settings"]["synthesizer"], "opencode")
        self.assertEqual((cfg["settings"]["framer"], cfg["settings"]["leader"]), ("agy", "agy"))

    def test_invalid_rosters_rejected(self):
        for requested in ("claude", "claude,claude", "claude,", "claude,unknown", ""):
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                select_agents(self.cfg, requested)


class AdapterIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def config(self, modes):
        agents = []
        for name, mode in zip(("alpha", "beta", "gamma"), modes):
            argv = [sys.executable, str(FIXTURE), name, mode, "{prompt}"]
            if mode in cli.FILE_MODES:
                argv.append("{outfile}")
            agents.append({"name": name, "output_mode": mode, "argv": argv, "env": {"SCENARIO": "question"}})
        return {"settings": {"max_rounds": 3, "per_agent_timeout_sec": 5}, "agents": agents}

    def write(self, cfg):
        path = self.root / "agents.yaml"
        path.write_text(yaml.safe_dump(cfg))
        return path

    def invoke(self, cfg, *extra):
        proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--config", str(self.write(cfg)),
                               "--sessions-dir", str(self.root / "sessions"), "--quiet", *extra,
                               "Explain this tradeoff"], capture_output=True, text=True, timeout=20)
        sessions = sorted((self.root / "sessions").glob("*/session.json"), key=os.path.getmtime)
        return proc, json.loads(sessions[-1].read_text()) if sessions else None

    def test_structured_and_history_outputs_through_full_debates(self):
        # The fixture asserts alpha's parsed round-1 evidence reaches every later prompt.
        for modes in (("gemini_json", "aider_history", "opencode_json"), ("qwen_json", "cursor_json", "stdout")):
            with self.subTest(modes=modes):
                proc, meta = self.invoke(self.config(modes))
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertTrue(meta["consensus"])
                self.assertEqual(meta["drop_history"], [])

    def test_aider_history_build_passes_verification(self):
        proc, meta = self.invoke(self.config(("aider_history", "gemini_json", "cursor_json")), "--type", "build")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(meta["build"]["status"], "passed")

    def test_agents_option_selects_a_roster_for_one_run(self):
        cfg = self.config(("stdout", "stdout", "stdout"))
        proc, meta = self.invoke(cfg, "--agents", "gamma,alpha")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(meta["agents"], ["gamma", "alpha"])
        self.assertEqual({c["name"] for c in meta["calls"]}, {"gamma", "alpha"})
        self.assertEqual([a["name"] for a in yaml.safe_load((self.root / "agents.yaml").read_text())["agents"]],
                         ["alpha", "beta", "gamma"])

    def test_invalid_agents_option_fails_before_a_session(self):
        proc, meta = self.invoke(self.config(("stdout", "stdout", "stdout")), "--agents", "alpha")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("min_agents", proc.stderr)
        self.assertIsNone(meta)

    def test_adapters_listing_needs_no_valid_config_and_calls_nothing(self):
        broken = self.root / "broken.yaml"
        broken.write_text("agents: [unclosed")
        proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--config", str(broken), "--adapters"],
                              capture_output=True, text=True, timeout=10, env=os.environ | {"PATH": str(self.root)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for name in ADAPTERS:
            self.assertIn(name, proc.stdout)
        self.assertNotIn(" yes ", proc.stdout)

    def test_setup_saves_a_selected_roster(self):
        path = self.write(self.config(("stdout", "stdout", "stdout")))
        with patch.object(sys.stdin, "isatty", return_value=True), patch.object(sys.stderr, "isatty", return_value=True), \
                patch("builtins.input", side_effect=["", "", "keep", "keep"]), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--setup", "--config", str(path), "--agents", "gamma,beta"]), 0)
        self.assertEqual([a["name"] for a in cli.load_config(path)["agents"]], ["gamma", "beta"])


if __name__ == "__main__":
    unittest.main()
