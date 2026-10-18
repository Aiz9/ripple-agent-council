"""Global configuration discovery and source/installed-entrypoint contracts."""
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

from agent_council import __version__
from agent_council import cli

PROJECT = Path(__file__).resolve().parents[1]


class GlobalToolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {"XDG_CONFIG_HOME": str(self.root / "config"),
                    "XDG_STATE_HOME": str(self.root / "state"),
                    "COUNCIL_CONFIG": "", "COUNCIL_SESSIONS_DIR": ""}
        override = patch.dict(os.environ, self.env)
        override.start()
        self.addCleanup(override.stop)

    def test_bundled_config_is_valid_without_user_config(self):
        selected = cli.config_path()
        self.assertEqual(selected, cli.ROOT / "agents.yaml")
        cfg = cli.load_config(selected)
        self.assertEqual([a["name"] for a in cfg["agents"]], ["claude", "codex", "agy", "hermes"])
        self.assertEqual(next(a for a in cfg["agents"] if a["name"] == "hermes")["model"], "")

    def test_init_config_is_idempotent_and_preserves_changes(self):
        with redirect_stdout(io.StringIO()):
            target = cli.init_config()
            target.write_text("# user changes\nsettings: {}\n")
            cli.init_config()
        self.assertEqual(target.read_text(), "# user changes\nsettings: {}\n")
        self.assertEqual(cli.config_path(), target)

    def test_config_precedence(self):
        with redirect_stdout(io.StringIO()):
            user = cli.init_config()
        environment = self.root / "environment.yaml"
        explicit = self.root / "explicit.yaml"
        self.assertEqual(cli.config_path(), user)
        with patch.dict(os.environ, COUNCIL_CONFIG=str(environment)):
            self.assertEqual(cli.config_path(), environment)
            self.assertEqual(cli.config_path(explicit), explicit)

    def test_invalid_user_config_does_not_fall_back_to_defaults(self):
        with redirect_stdout(io.StringIO()):
            user = cli.init_config()
        user.write_text("not a mapping")
        self.assertEqual(cli.config_path(), user)
        with self.assertRaises(cli.ConfigError):
            cli.load_config(cli.config_path())

    def test_init_config_respects_explicit_destination(self):
        target = self.root / "custom" / "agents.yaml"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--init-config", "--config", str(target)]), 0)
        self.assertTrue(target.is_file())
        self.assertFalse(cli.user_config_path().exists())

    def test_init_config_rejects_directory(self):
        with self.assertRaises(cli.ConfigError):
            cli.init_config(self.root)

    def test_session_precedence(self):
        self.assertEqual(cli.sessions_path(), self.root / "state/agent-council/sessions")
        with patch.dict(os.environ, COUNCIL_SESSIONS_DIR=str(self.root / "environment")):
            self.assertEqual(cli.sessions_path(), self.root / "environment")
            self.assertEqual(cli.sessions_path(str(self.root / "explicit")), self.root / "explicit")

    def test_relative_and_empty_xdg_paths_use_home_defaults(self):
        with patch.object(Path, "home", return_value=self.root):
            for value in ("", "relative/directory"):
                with patch.dict(os.environ, XDG_CONFIG_HOME=value, XDG_STATE_HOME=value):
                    self.assertEqual(cli.user_config_path(), self.root / ".config/agent-council/agents.yaml")
                    self.assertEqual(cli.sessions_path(), self.root / ".local/state/agent-council/sessions")

    def test_paths_is_read_only_and_does_not_validate_or_call_models(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--paths", "--config", str(self.root / "not-created.yaml")])
        self.assertEqual(code, 0)
        paths = json.loads(out.getvalue())
        self.assertEqual(paths["config"], str(self.root / "not-created.yaml"))
        self.assertFalse((self.root / "config").exists())
        self.assertFalse((self.root / "state").exists())

    def test_source_entrypoint_from_unrelated_directory(self):
        # A file named agents.yaml in the caller's cwd must not hijack global config.
        (self.root / "agents.yaml").write_text("invalid config")
        proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--paths"],
                              cwd=self.root, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(proc.stdout)["config"], str(cli.ROOT / "agents.yaml"))
        self.assertEqual(list(self.root.iterdir()), [self.root / "agents.yaml"])

    def test_module_entrypoint_version_without_config(self):
        proc = subprocess.run([sys.executable, "-m", "agent_council", "--version"],
                              cwd=PROJECT, capture_output=True, text=True, check=True)
        self.assertEqual(proc.stdout.strip(), f"council {__version__}")

    def test_package_metadata_and_runtime_versions_match(self):
        import tomllib
        project = tomllib.loads((PROJECT / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["version"], __version__)
        self.assertEqual(project["scripts"]["council"], "agent_council.cli:main")


if __name__ == "__main__":
    unittest.main()
