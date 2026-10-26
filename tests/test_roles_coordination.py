import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

import yaml

from agent_council import cli
from agent_council.roles import PRESETS, assign_role, resolve_role, role_catalog
from agent_council.setup import configure, save_configuration

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("coordination_agent.py").resolve()


class RoleCoordinationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "agents.yaml"
        self.cfg = {
            "settings": {"mode": "peer-to-peer", "leader": "alpha", "max_rounds": 3},
            "agents": [{"name": name, "argv": [sys.executable, str(FIXTURE), name, "{prompt}"],
                        "role_preset": role, "output_mode": "stdout", "timeout_sec": 5}
                       for name, role in (("alpha", "architect"), ("beta", "implementer"), ("gamma", "skeptic"))],
        }

    def write(self):
        self.path.write_text(yaml.safe_dump(self.cfg))
        return self.path

    def invoke(self, mode="peer-to-peer", scenario="normal", extra=(), leader="alpha"):
        self.cfg["settings"].update(mode=mode, leader=leader)
        for agent in self.cfg["agents"]:
            agent["env"] = {"SCENARIO": scenario, "LEADER": leader}
        self.write()
        existing = set((self.root / "sessions").glob("*/session.json"))
        process = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--config", str(self.path),
                                  "--sessions-dir", str(self.root / "sessions"), "--non-interactive", "--quiet",
                                  *extra, "Discuss this problem"], capture_output=True, text=True, timeout=15)
        files = list(set((self.root / "sessions").glob("*/session.json")) - existing)
        meta = json.loads(files[-1].read_text()) if files else None
        return process, meta, files[-1].parent if files else None

    def test_presets_custom_names_and_legacy_inline_role(self):
        self.cfg["roles"] = {"data-expert": "Review data quality and statistical assumptions."}
        self.cfg["agents"][0]["role_preset"] = "data-expert"
        agent = self.cfg["agents"][1]
        agent.pop("role_preset")
        agent["role"] = "My legacy role"
        cfg = cli.load_config(self.write())
        catalog = role_catalog(cfg)
        self.assertIn("security", catalog)
        self.assertEqual(resolve_role(cfg["agents"][0], catalog)[1], self.cfg["roles"]["data-expert"])
        self.assertEqual(resolve_role(cfg["agents"][1], catalog), ("custom", "My legacy role"))

    def test_invalid_roles_and_mode_rejected(self):
        base = copy.deepcopy(self.cfg)
        changes = [lambda c: c["agents"][0].update(role_preset="missing"),
                   lambda c: c["agents"][0].update(role="conflicting custom role"),
                   lambda c: c["agents"][0].update(role=123),
                   lambda c: c.update(roles={"architect": "override"}),
                   lambda c: c.update(roles={"custom": "reserved"}),
                   lambda c: c.update(roles={"named": ""}),
                   lambda c: c.update(roles=[]),
                   lambda c: c["settings"].update(mode="invalid"),
                   lambda c: c["settings"].update(peer_turns="random"),
                   lambda c: c["settings"].update(leader="missing")]
        for change in changes:
            self.cfg = copy.deepcopy(base)
            change(self.cfg)
            with self.subTest(change=change), self.assertRaises(cli.ConfigError):
                cli.load_config(self.write())

    def test_assign_roles_does_not_touch_model_or_command(self):
        before = copy.deepcopy(self.cfg["agents"][0])
        assign_role(self.cfg, "alpha=tester")
        self.assertEqual(self.cfg["agents"][0]["role_preset"], "tester")
        assign_role(self.cfg, "alpha=Investigate Qwen-specific edge cases.", custom=True)
        agent = self.cfg["agents"][0]
        self.assertNotIn("role_preset", agent)
        self.assertEqual(agent["argv"], before["argv"])
        self.assertEqual(agent.get("model"), before.get("model"))
        for assignment in ("missing=tester", "alpha=missing", "alpha=", "alpha"):
            with self.assertRaises(ValueError):
                assign_role(self.cfg, assignment)

    def test_peer_conversation_delivers_direct_same_round_replies(self):
        process, meta, session = self.invoke()
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue(meta["consensus"])
        self.assertEqual(meta["mode"], "peer-to-peer")
        self.assertEqual(meta["peer_turns"], "sequential")
        self.assertIsNone(meta["leader"])
        self.assertEqual(len(meta["calls"]), 11)
        self.assertIn("ALPHA_QUESTION", (session / "rounds/round2.beta.prompt.txt").read_text())
        self.assertIn("BETA_REPLY", (session / "rounds/round2.gamma.prompt.txt").read_text())
        self.assertIn("BETA_REPLY", (session / "rounds/round3.alpha.prompt.txt").read_text())

    def test_parallel_peer_turns_share_only_completed_rounds(self):
        process, meta, session = self.invoke(extra=["--peer-turns", "parallel"])
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue(meta["consensus"])
        self.assertEqual(meta["peer_turns"], "parallel")
        self.assertEqual(len(meta["calls"]), 11)
        # The fixture also asserts these visibility rules from inside every turn.
        round2 = (session / "rounds/round2.gamma.prompt.txt").read_text()
        self.assertIn("PEER_EVIDENCE_alpha", round2)
        self.assertNotIn("PARALLEL_POINT_alpha", round2)
        self.assertIn("PARALLEL_POINT_alpha", (session / "rounds/round3.gamma.prompt.txt").read_text())
        self.assertIn("(parallel turns)", (session / "final.md").read_text())

    def test_hierarchy_assigns_reviews_and_uses_leader_for_final(self):
        process, meta, session = self.invoke("hierarchical", leader="beta")
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue(meta["consensus"])
        self.assertEqual(meta["leader"], "beta")
        self.assertIsNone(meta["peer_turns"])
        self.assertEqual(meta["synthesizer"], "beta")
        self.assertEqual(len(meta["calls"]), 14)
        worker = (session / "rounds/round2.alpha.prompt.txt").read_text()
        self.assertIn("ASSIGNMENT_FOR_alpha", worker)
        self.assertNotIn("SPECIALIST_EVIDENCE_gamma", worker)
        review = (session / "rounds/round2.beta.prompt.txt").read_text()
        self.assertIn("SPECIALIST_EVIDENCE_alpha", review)
        self.assertIn("SPECIALIST_EVIDENCE_gamma", review)

    def test_hierarchy_preserves_specialist_dissent(self):
        process, meta, _ = self.invoke("hierarchical", "dissent")
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertFalse(meta["consensus"])

    def test_hierarchy_pauses_when_no_leader_can_complete_the_phase(self):
        for scenario in ("plan_fail", "review_fail"):
            with self.subTest(scenario=scenario):
                process, meta, _ = self.invoke("hierarchical", scenario)
                self.assertEqual(process.returncode, 75, process.stdout)
                self.assertEqual(meta["status"], "paused")
                self.assertIn("alpha", meta["dropped"])

    def test_hierarchy_tries_all_successors_before_pausing_final(self):
        process, meta, _ = self.invoke("hierarchical", "final_fail")
        self.assertEqual(process.returncode, 75)
        self.assertEqual(meta["status"], "paused")
        self.assertEqual([c["name"] for c in meta["calls"] if c["tag"] == "final"], ["alpha", "beta", "gamma"])

    def test_hierarchy_worker_drop_continues_with_quorum(self):
        process, meta, _ = self.invoke("hierarchical", "worker_drop")
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertTrue(meta["consensus"])
        self.assertIn("gamma", meta["dropped"])
        self.assertEqual(meta["rounds"][-1]["ok"], ["beta", "alpha"])

    def test_hierarchy_build_verification_still_executes(self):
        process, meta, _ = self.invoke("hierarchical", extra=["--type", "build"])
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertEqual(meta["build"]["status"], "passed")

    def test_cli_role_overrides_appear_in_prompts_and_session(self):
        process, meta, session = self.invoke(extra=["--role", "alpha=tester", "--custom-role", "beta=Inspect migration compatibility."])
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertEqual(meta["roles"]["alpha"]["name"], "tester")
        self.assertEqual(meta["roles"]["beta"]["description"], "Inspect migration compatibility.")
        self.assertIn("Inspect migration compatibility.", (session / "rounds/round1.beta.prompt.txt").read_text())
        self.assertEqual(yaml.safe_load(self.path.read_text())["agents"][0]["role_preset"], "architect")

    def test_conflicting_cli_assignments_rejected_before_session(self):
        process, meta, _ = self.invoke(extra=["--role", "alpha=tester", "--custom-role", "alpha=Conflict"])
        self.assertEqual(process.returncode, 2)
        self.assertIsNone(meta)

    def test_missing_leader_attempts_successors_before_pausing(self):
        self.cfg["agents"][0]["argv"][0] = "council-missing-leader"
        process, meta, _ = self.invoke("hierarchical")
        self.assertEqual(process.returncode, 75)
        self.assertEqual(meta["status"], "paused")

    def test_wizard_selects_hierarchy_and_custom_roles(self):
        cfg = cli.load_config(self.write())
        with patch("builtins.input", side_effect=["2", "beta", "security", "custom", "", "Own the implementation.", "keep"]), redirect_stdout(io.StringIO()):
            selected = configure(cfg)
        self.assertEqual(selected["settings"]["mode"], "hierarchical")
        self.assertEqual(selected["settings"]["leader"], "beta")
        self.assertEqual(selected["agents"][0]["role_preset"], "security")
        self.assertEqual(selected["agents"][1]["role"], "Own the implementation.")
        self.assertEqual(cfg["settings"]["mode"], "peer-to-peer")

    def test_wizard_peer_mode_asks_turn_style(self):
        cfg = cli.load_config(self.write())
        with patch("builtins.input", side_effect=["1", "parallel", "keep", "keep", "keep"]), redirect_stdout(io.StringIO()):
            selected = configure(cfg)
        self.assertEqual(selected["settings"]["mode"], "peer-to-peer")
        self.assertEqual(selected["settings"]["peer_turns"], "parallel")
        self.assertEqual(cfg["settings"]["peer_turns"], "sequential")

    def test_setup_save_keeps_backup_and_cli_configuration(self):
        cfg = cli.load_config(self.write())
        original = self.path.read_bytes()
        cfg["agents"][0].pop("role_preset")
        cfg["agents"][0]["role"] = "Custom responsibilities"
        backup = save_configuration(cfg, self.path)
        self.assertEqual(backup.read_bytes(), original)
        stored = cli.load_config(self.path)
        self.assertEqual(stored["agents"][0]["role"], "Custom responsibilities")
        self.assertEqual(stored["agents"][0]["argv"], cfg["agents"][0]["argv"])

    def test_automatic_terminal_questions_precede_execution(self):
        cfg_path = self.write()
        with patch.object(sys.stdin, "isatty", return_value=True), patch.object(sys.stderr, "isatty", return_value=True), \
                patch("builtins.input", side_effect=["n", "2", "beta", "keep", "keep", "keep"]) as ask, \
                patch.object(cli, "run_council", return_value=0) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--config", str(cfg_path), "question"]), 0)
        self.assertEqual(ask.call_count, 6)
        self.assertEqual(run.call_args.args[1]["settings"]["leader"], "beta")
        self.assertEqual(yaml.safe_load(self.path.read_text())["settings"]["mode"], "peer-to-peer")

    def test_saved_choices_are_confirmed_with_one_answer(self):
        cfg_path = self.write()
        with patch.object(sys.stdin, "isatty", return_value=True), patch.object(sys.stderr, "isatty", return_value=True), \
                patch("builtins.input", side_effect=[""]) as ask, \
                patch.object(cli, "run_council", return_value=0) as run, redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["--config", str(cfg_path), "question"]), 0)
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(run.call_args.args[1]["settings"]["mode"], "peer-to-peer")
        self.assertIn("alpha=architect, beta=implementer, gamma=skeptic", out.getvalue())
        self.assertNotIn("Using these choices", out.getvalue())

    def test_explicit_mode_and_noninteractive_skip_questions(self):
        cfg_path = self.write()
        for extra in (["--mode", "hierarchical"], ["--non-interactive"]):
            with patch.object(sys.stdin, "isatty", return_value=True), patch.object(sys.stderr, "isatty", return_value=True), \
                    patch("builtins.input", side_effect=AssertionError("must not ask")), \
                    patch.object(cli, "run_council", return_value=0), redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["--config", str(cfg_path), *extra, "question"]), 0)

    def test_nonterminal_setup_returns_error_without_hanging(self):
        process = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--setup", "--config", str(self.write())],
                                 capture_output=True, text=True, timeout=5)
        self.assertEqual(process.returncode, 2)
        self.assertIn("requires an interactive terminal", process.stderr)

    def test_cancelled_setup_leaves_configuration_untouched(self):
        self.write()
        original = self.path.read_bytes()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), \
                patch.object(sys.stdin, "isatty", return_value=True), patch.object(sys.stderr, "isatty", return_value=True), \
                patch("builtins.input", side_effect=KeyboardInterrupt):
            self.assertEqual(cli.main(["--setup", "--config", str(self.path)]), 130)
        self.assertEqual(self.path.read_bytes(), original)

    @unittest.skipUnless(os.name == "posix", "terminal setup uses a POSIX PTY")
    def test_real_terminal_setup_saves_mode_leader_and_roles(self):
        import pty
        master, slave = pty.openpty()
        process = subprocess.Popen([sys.executable, str(PROJECT / "council.py"), "--setup", "--config", str(self.write())],
                                   stdin=slave, stderr=slave, stdout=subprocess.PIPE, text=True)
        os.close(slave)
        try:
            os.write(master, b"2\nbeta\nsecurity\ncustom\nOwn implementation quality.\nkeep\n")
            stdout, _ = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stdout)
            saved = cli.load_config(self.path)
            self.assertEqual(saved["settings"]["mode"], "hierarchical")
            self.assertEqual(saved["settings"]["leader"], "beta")
            self.assertEqual(saved["agents"][0]["role_preset"], "security")
            self.assertEqual(saved["agents"][1]["role"], "Own implementation quality.")
            self.assertEqual(len(list(self.root.glob("agents.yaml.backup-*"))), 1)
            self.assertFalse((self.root / "sessions").exists())
        finally:
            os.close(master)
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_role_listing_does_not_start_agents(self):
        self.cfg["roles"] = {"domain-expert": "Domain-specific review"}
        with patch.object(cli, "run_council", side_effect=AssertionError("no calls")), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["--roles", "--config", str(self.write())]), 0)
        self.assertIn("domain-expert", out.getvalue())
        for name in PRESETS:
            self.assertIn(name, out.getvalue())


if __name__ == "__main__":
    unittest.main()
