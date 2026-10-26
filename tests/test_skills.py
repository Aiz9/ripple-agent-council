"""Shared library installation, portability, and every-agent prompt delivery."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from agent_council import cli, skills
from test_council import PROJECT_ROOT, TemporaryTest


class SkillTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.library = self.root / "library"
        environment = patch.dict(os.environ, {"COUNCIL_SKILLS_DIR": str(self.library)})
        environment.start()
        self.addCleanup(environment.stop)

    def skill(self, root=None, name="example", folder="03-category/example", body="Use evidence and run tests."):
        path = (root or self.library) / folder / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: {name}\ndescription: Use when testing a design.\n---\n# Example\n\n{body}\n",
                        encoding="utf-8")
        references = path.parent / "references"
        references.mkdir(exist_ok=True)
        (references / "guide.md").write_text("Supporting reference", encoding="utf-8")
        return path

    def test_global_directory_precedence(self):
        self.assertEqual(skills.directory(), self.library)
        self.assertEqual(skills.directory(str(self.root / "explicit")), self.root / "explicit")
        with patch.dict(os.environ, {"COUNCIL_SKILLS_DIR": "", "XDG_DATA_HOME": str(self.root / "data")}):
            self.assertEqual(skills.directory(), self.root / "data/agent-council/skills")
        with patch.dict(os.environ, {"COUNCIL_SKILLS_DIR": "", "XDG_DATA_HOME": "relative"}), \
                patch.object(Path, "home", return_value=self.root):
            self.assertEqual(skills.directory(), self.root / ".local/share/agent-council/skills")

    def test_validation_is_idempotent_and_preserved_by_roster_selection(self):
        cfg = self.config() | {"skills": {"names": ["example"], "directory": str(self.library)}}
        validated = cli.validate_config(cfg)
        self.assertEqual(cli.validate_config(copy.deepcopy(validated)), validated)
        selected = cli.select_agents(validated, "alpha,beta")
        self.assertEqual(selected["skills"], validated["skills"])

    def test_invalid_settings(self):
        for value in ([], {"enabled": "yes"}, {"directory": ""}, {"names": "example"},
                      {"names": ["../escape"]}, {"names": ["example", "example"]},
                      {"names": ["*", "example"]}, {"surprise": True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                skills.validate(value)

    def test_missing_default_library_is_optional_but_explicit_choices_are_required(self):
        with patch.dict(os.environ, {"COUNCIL_SKILLS_DIR": "", "XDG_DATA_HOME": str(self.root / "data")}):
            self.assertEqual(skills.select(skills.validate(None))[1], [])
        with self.assertRaisesRegex(ValueError, "does not exist"):
            skills.select(skills.validate(None))  # explicit environment override
        with self.assertRaisesRegex(ValueError, "does not exist"):
            skills.select(skills.validate({"directory": str(self.library)}))
        with self.assertRaises(ValueError):
            skills.select(skills.validate({"names": ["example"]}))
        self.assertEqual(skills.select(skills.validate({"enabled": False}))[1], [])

    def test_discovery_handles_nested_skills_and_upstream_description_continuations(self):
        self.skill(name="second", folder="category/second")
        path = self.skill()
        path.write_text("---\nname: example\ndescription: First line.\nUse when researching.\nversion: 1\n---\nBody\n")
        items = skills.discover(self.library)
        self.assertEqual([item["name"] for item in items], ["example", "second"])
        self.assertEqual(items[0]["description"], "First line. Use when researching.")

    def test_discovery_rejects_duplicate_names_and_symlinks(self):
        path = self.skill()
        other = self.skill(folder="other")
        with self.assertRaisesRegex(ValueError, "duplicate skill"):
            skills.discover(self.library)
        shutil.rmtree(other.parent)
        (path.parent / "escape").symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            skills.discover(self.library)

    def test_selection_uses_frontmatter_names(self):
        self.skill(name="peft-fine-tuning", folder="03-fine-tuning/peft")
        self.skill(name="other", folder="other")
        items = skills.select(skills.validate({"names": ["peft-fine-tuning"]}))[1]
        self.assertEqual([item["name"] for item in items], ["peft-fine-tuning"])
        with self.assertRaisesRegex(ValueError, "unknown skills"):
            skills.select(skills.validate({"names": ["typo"]}))

    def test_install_local_checkout_copies_resources_license_and_provenance(self):
        source = self.root / "source"
        original = self.skill(root=source)
        (source / "LICENSE").write_text("MIT license fixture")
        with patch.object(skills.subprocess, "run") as run:
            target, count = skills.install(self.library, str(source))
        run.assert_not_called()
        self.assertEqual(count, 1)
        self.assertEqual((target / original.relative_to(source)).read_text(), original.read_text())
        self.assertEqual((target / "03-category/example/references/guide.md").read_text(), "Supporting reference")
        self.assertEqual((target / "LICENSE").read_text(), "MIT license fixture")
        provenance = json.loads((target / "council-source.json").read_text())
        self.assertEqual(provenance["source"], str(source))
        with self.assertRaisesRegex(ValueError, "already exists"):
            skills.install(self.library, str(source))
        self.assertEqual(len(skills.discover(self.library)), 1)

    def test_all_four_sources_install_independently_and_can_be_retried(self):
        upstream = {}
        for name, url in skills.SOURCES.items():
            root = self.root / "upstream" / name
            folder = "03-category/example" if name == "orchestra-research" else f"skills/{name}"
            self.skill(root=root, name=name, folder=folder)
            (root / "LICENSE").write_text("License fixture")
            upstream[url] = root
        cloned = []
        def git(command, **kwargs):
            if command[1] == "clone":
                url, target = command[-2:]
                cloned.append(url)
                shutil.copytree(upstream[url], target)
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 0, "abc123\n", "")
        config = self.write_config(self.config())
        with patch.object(skills.subprocess, "run", side_effect=git), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            for _ in range(2):
                self.assertEqual(cli.main(["--config", str(config), "--install-skills"]), 0)
        self.assertEqual(cloned, list(skills.SOURCES.values()))
        self.assertEqual(len(skills.discover(self.library)), 4)
        for name in skills.SOURCES:
            provenance = json.loads((self.library / name / "council-source.json").read_text())
            self.assertEqual(provenance["revision"], "abc123")

    def test_canonical_skill_install_skips_native_duplicates_and_preserves_lowercase_documents(self):
        source = self.root / "graphify"
        original = self.skill(root=source, name="graphify", folder="skills/graphify")
        original.rename(original.with_name("skill.md"))
        self.skill(root=source, name="graphify", folder="i18n/duplicate")
        (source / ".native").symlink_to(self.root)
        target, count = skills.install(self.library, str(source))
        self.assertEqual(count, 1)
        self.assertFalse((target / "i18n").exists())
        self.assertFalse((target / ".native").exists())
        config = skills.validate(None)
        session = self.root / "session"
        session.mkdir()
        skills.snapshot(config, session, skills.select(config))
        saved = json.loads((session / "skills.json").read_text())["skills"][0]
        path = session / saved["path"]
        self.assertEqual(path.name, "SKILL.md")
        self.assertIn(skills.COMPATIBILITY, path.read_text())
        self.assertEqual(path.read_text(), path.with_name("skill.md").read_text())
        self.assertTrue(path.with_name("SKILL.original.md").exists())

    def test_caveman_generated_adapters_are_excluded_but_shared_core_is_snapshotted(self):
        source = self.root / "caveman"
        self.skill(root=source, name="caveman", folder="skills/caveman")
        self.skill(root=source, name="caveman", folder="skills/generated/codex/caveman")
        (source / "skills/native-core.md").write_text("Shared core instructions")
        target, count = skills.install(self.library, str(source))
        self.assertEqual(count, 1)
        self.assertFalse((target / "skills/generated").exists())
        session = self.root / "session"
        session.mkdir()
        config = skills.validate({"names": ["caveman"]})
        skills.snapshot(config, session, skills.select(config))
        self.assertEqual((session / "skills/caveman/skills/native-core.md").read_text(), "Shared core instructions")

    def test_failed_download_never_publishes_partial_library(self):
        failed = subprocess.CompletedProcess([], 1, "", "network unavailable")
        with patch.object(skills.subprocess, "run", return_value=failed), \
                self.assertRaisesRegex(ValueError, "could not download skills.*network unavailable"):
            skills.install(self.library)
        self.assertEqual(list(self.library.iterdir()), [])

    def test_install_rejects_unsupported_sources_and_recursive_destination(self):
        source = self.root / "source"
        self.skill(root=source)
        for destination, value in ((self.library, "https://example.invalid/untrusted.git"),
                                   (source / "nested", str(source))):
            with self.subTest(source=value), self.assertRaises(ValueError):
                skills.install(destination, value)

    def test_portability_converts_native_paths_and_agent_continuity(self):
        text = ("---\nname: example\ndescription: Example.\n---\n"
                "Use ~/.claude/skills/example/references/guide.md and $HOME/.codex/skills/example/SKILL.md.\n"
                "## Agent Continuity (MANDATORY)\n### Claude Code\n/loop 20m research\n"
                "### OpenClaw\ncron.add must run\n## Next\nKeep this domain guidance.\n")
        portable = skills.portable_document(text, {"example": "/session/skills/example"})
        self.assertTrue(portable.startswith("---\nname: example"))
        self.assertIn("/session/skills/example/references/guide.md", portable)
        self.assertIn("/session/skills/example/SKILL.md", portable)
        self.assertNotIn("/loop 20m research", portable)
        self.assertNotIn("cron.add must run", portable)
        self.assertIn("Council owns continuation", portable)
        self.assertIn("Keep this domain guidance.", portable)
        self.assertIn("configured Council peers", portable)
        self.assertIn("If unavailable, report the limitation", portable)

    def test_native_paths_support_multiple_configured_agent_conventions(self):
        for root in ("~/.claude", "$HOME/.codex", "/home/example/.gemini", "~/.hermes", ".agents",
                     "~/.cursor", "~/.qwen", "~/.kiro", "~/.goose", "~/.copilot", "~/.aider",
                     "~/.config/opencode", "~/.gemini/antigravity"):
            with self.subTest(root=root):
                text = f"Read {root}/skills/example/references/guide.md"
                portable = skills.portable_document(text, {"example": "/saved/example"})
                self.assertIn("Read /saved/example/references/guide.md", portable)
                self.assertNotIn(root + "/skills/example", portable)

    def test_graphify_native_requirements_are_adapted(self):
        text = ('**MANDATORY: You MUST use the Agent tool here. Native-only requirement.**\n'
                'python3 -c "import graphify" 2>/dev/null || pip install graphifyy --break-system-packages\n')
        portable = skills.portable_document(text, {})
        self.assertNotIn("You MUST use the Agent tool", portable)
        self.assertNotIn("--break-system-packages", portable)
        self.assertIn("configured Council peers", portable)
        self.assertIn("bounded chunk yourself", portable)
        self.assertIn("isolated environment", portable)

    def test_default_style_skills_are_inlined_without_inlining_graphify(self):
        for name in ("ponytail", "caveman", "graphify"):
            self.skill(name=name, folder=f"skills/{name}", body=f"{name.upper()}_INSTRUCTIONS")
        session = self.root / "session"
        session.mkdir()
        config = skills.validate(None)
        skills.snapshot(config, session, skills.select(config))
        context, names = skills.context(session)
        self.assertEqual(names, ["caveman", "graphify", "ponytail"])
        self.assertIn("PONYTAIL_INSTRUCTIONS", context)
        self.assertIn("CAVEMAN_INSTRUCTIONS", context)
        self.assertNotIn("GRAPHIFY_INSTRUCTIONS", context)
        self.assertIn("complete build files", context)
        self.assertIn("CONSENSUS", context)

    def test_malformed_checkpoint_has_a_clear_error(self):
        session = self.root / "session"
        session.mkdir()
        (session / "skills.json").write_text(json.dumps({"schema_version": 1, "skills": [{}]}))
        with self.assertRaisesRegex(ValueError, "invalid skill record"):
            skills.context(session)

    def test_snapshot_preserves_originals_resources_and_resumes_without_library(self):
        original = self.skill(body="Read ~/.claude/skills/example/references/guide.md.")
        original_text = original.read_text()
        session = self.root / "session"
        session.mkdir()
        config = skills.validate({"names": ["example"]})
        skills.snapshot(config, session, skills.select(config))
        before, names = skills.context(session)
        self.assertEqual(names, ["example"])
        self.assertIn("CONFIGURED SKILL: example", before)
        adapted = session / "skills/03-category/example/SKILL.md"
        self.assertEqual(adapted.with_name("SKILL.original.md").read_text(), original_text)
        self.assertTrue((adapted.parent / "references/guide.md").exists())
        shutil.rmtree(self.library)
        self.assertEqual(skills.context(session), (before, names))
        adapted.write_text("changed")
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            skills.context(session)

    def test_all_skills_use_catalog_without_inlining_the_whole_library(self):
        self.skill(body="Long domain instructions")
        session = self.root / "session"
        session.mkdir()
        config = skills.validate(None)
        skills.snapshot(config, session, skills.select(config))
        context, _ = skills.context(session)
        self.assertIn('"name": "example"', context)
        self.assertNotIn("Long domain instructions", context)
        self.assertIn("Before answering, read and apply", context)

    def test_old_sessions_do_not_gain_new_global_skills(self):
        self.skill()
        self.assertEqual(skills.context(self.root / "old-session"), ("", []))

    def test_no_skills_preserves_old_prompt_bytes(self):
        agent = {"name": "alpha", "role": "researcher", "council_context": "TEAM"}
        expected = (f"You are council agent alpha. Role: researcher\n\n{cli.ANTI_HALLUCINATION}"
                    "\n\nUSER GOAL:\nGoal\n\nDEFINITION OF DONE:\nDone\n\nTEAM\nBe concise and specific.")
        self.assertEqual(cli.preamble(agent, "Goal", "Done"), expected)

    def invoke(self, *args, config=None):
        command = [sys.executable, str(PROJECT_ROOT / "council.py"), "--sessions-dir", str(self.root / "sessions"),
                   "--non-interactive", "--quiet"]
        if config is not None:
            command += ["--config", str(self.write_config(config))]
        return subprocess.run(command + list(args), capture_output=True, text=True, timeout=20)

    def newest(self):
        return max((self.root / "sessions").iterdir(), key=lambda path: path.stat().st_mtime_ns)

    def test_global_defaults_reach_every_agent_and_every_phase(self):
        self.skill()
        proc = self.invoke("--anonymous-review", "Explain this tradeoff", config=self.config())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        session = self.newest()
        meta = json.loads((session / "session.json").read_text())
        self.assertEqual(meta["skills"], ["example"])
        for call in meta["calls"]:
            prompt = (session / "rounds" / f"{call['tag']}.{call['name']}.prompt.txt").read_text()
            self.assertIn("SHARED COUNCIL SKILLS", prompt)
            self.assertIn("COUNCIL SKILL COMPATIBILITY", prompt)
            self.assertIn(f"Your runtime is {call['name']}", prompt)
            self.assertIn('"name": "example"', prompt)

    def test_hierarchical_leader_and_specialists_receive_portable_skills(self):
        self.skill()
        cfg = self.config()
        fixture = Path(__file__).with_name("coordination_agent.py").resolve()
        for agent in cfg["agents"]:
            agent.update(argv=[sys.executable, str(fixture), agent["name"], "{prompt}"],
                         output_mode="stdout", env={"LEADER": "alpha"})
        proc = self.invoke("--mode", "hierarchical", "--leader", "alpha", "Discuss this problem", config=cfg)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        prompts = list((self.newest() / "rounds").glob("*.prompt.txt"))
        self.assertTrue(any(path.name.startswith("plan1.") for path in prompts))
        for prompt in prompts:
            self.assertIn("SHARED COUNCIL SKILLS", prompt.read_text())
            self.assertIn("COUNCIL SKILL COMPATIBILITY", prompt.read_text())

    def test_explicit_skill_is_inlined_and_can_be_disabled(self):
        self.skill(body="UNIQUE_DOMAIN_INSTRUCTIONS")
        proc = self.invoke("--skill", "example", "Explain this tradeoff", config=self.config())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        session = self.newest()
        self.assertIn("UNIQUE_DOMAIN_INSTRUCTIONS", (session / "rounds/round0.alpha.prompt.txt").read_text())
        proc = self.invoke("--no-skills", "Explain this tradeoff", config=self.config())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("SHARED COUNCIL SKILLS", (self.newest() / "rounds/round0.alpha.prompt.txt").read_text())

    def test_configured_skills_are_used_by_default(self):
        self.skill(body="CONFIGURED_BY_DEFAULT")
        self.skill(name="other", folder="other", body="NOT_SELECTED")
        cfg = self.config() | {"skills": {"names": ["example"]}}
        proc = self.invoke("Explain this tradeoff", config=cfg)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        prompt = (self.newest() / "rounds/round0.alpha.prompt.txt").read_text()
        self.assertIn("CONFIGURED_BY_DEFAULT", prompt)
        self.assertNotIn("NOT_SELECTED", prompt)

    def test_resume_uses_adapted_snapshot_after_global_library_deletion(self):
        self.skill(body="RESUME_DOMAIN_INSTRUCTIONS")
        proc = self.invoke("--skill", "example", "--max-cost", "0.1", "Explain this tradeoff", config=self.config())
        self.assertEqual(proc.returncode, 75, proc.stdout + proc.stderr)
        session = self.newest()
        shutil.rmtree(self.library)
        resumed = self.invoke("--resume", str(session), "--max-cost", "10")
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        self.assertIn("RESUME_DOMAIN_INSTRUCTIONS", (session / "rounds/final.alpha.prompt.txt").read_text())
        self.assertGreater(json.loads((session / "session.json").read_text())["reused_calls"], 0)

    def test_missing_selected_skill_fails_before_session_or_calls(self):
        proc = self.invoke("--skill", "missing", "Explain this tradeoff", config=self.config())
        self.assertEqual(proc.returncode, 2)
        self.assertFalse((self.root / "sessions").exists())

    def test_install_and_list_commands_make_no_model_calls(self):
        source = self.root / "source"
        self.skill(root=source)
        proc = self.invoke("--install-skills", str(source))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        proc = self.invoke("--skills")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("* example:", proc.stdout)
        self.assertFalse((self.root / "sessions").exists())

    def test_mcp_requests_inherit_shared_skill_overrides(self):
        from agent_council.mcp_server import Server
        alternate = self.root / "alternate-library"
        self.skill(root=alternate, body="MCP_CONFIGURED_SKILL")
        server = Server(self.root / "sessions", self.write_config(self.config()),
                        skill_options=["--skills-dir", str(alternate), "--skill", "example"])
        _, data = server.ask_council({"prompt": "Explain this tradeoff"})
        self.assertEqual(data["status"], "completed")
        self.assertIn("MCP_CONFIGURED_SKILL", (self.newest() / "rounds/round0.alpha.prompt.txt").read_text())

    def test_resume_rejects_changed_skill_options(self):
        for args in (("--skill", "example"), ("--no-skills",), ("--skills-dir", str(self.library))):
            with self.subTest(args=args):
                proc = self.invoke("--resume", "latest", *args)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("--resume restores", proc.stderr)
