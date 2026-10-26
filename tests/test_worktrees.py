"""--repo: per-agent git worktrees and a verified build committed on its own branch."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fake_agent.py").resolve()


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class WorktreeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()  # git and the CLI report resolved paths
        # No user identity anywhere: the fallback identity must be used.
        self.env = os.environ | {"GIT_CONFIG_GLOBAL": str(self.root / "no-gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
        self.repo = self.root / "project"
        self.repo.mkdir()
        git("init", "-q", "-b", "main", cwd=self.repo)
        (self.repo / "README.md").write_text("project\n")
        git("add", "README.md", cwd=self.repo)
        git("-c", "user.name=Owner", "-c", "user.email=owner@example.com", "commit", "-qm", "initial", cwd=self.repo)
        self.base = git("rev-parse", "HEAD", cwd=self.repo)

    def run_council(self, scenario, *args):
        agents = []
        for name, mode in (("alpha", "claude_json"), ("beta", "file"), ("gamma", "stdout")):
            argv = [sys.executable, str(FIXTURE), name, mode, "{prompt}"] + (["{outfile}"] if mode == "file" else [])
            agents.append({"name": name, "output_mode": mode, "argv": argv, "env": {"SCENARIO": scenario}})
        config = self.root / "agents.yaml"
        config.write_text(yaml.safe_dump({"settings": {"max_rounds": 3, "per_agent_timeout_sec": 5},
                                          "agents": agents}))
        proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--config", str(config), "--sessions-dir",
                               str(self.root / "s"), "--non-interactive", "--quiet", *args],
                              capture_output=True, text=True, timeout=60, env=self.env, cwd=self.root)
        sessions = list((self.root / "s").glob("*/session.json"))
        return proc, (sessions[0].parent if sessions else None)

    def worktree_paths(self):
        return [line[9:] for line in git("worktree", "list", "--porcelain", cwd=self.repo).splitlines()
                if line.startswith("worktree ")]

    def test_build_is_committed_on_a_council_branch(self):
        proc, session = self.run_council("build", "--type", "build", "--repo", str(self.repo / "."),
                                         "Build a calculator")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        branch = f"council/{session.name}"
        meta = json.loads((session / "session.json").read_text())
        self.assertEqual(meta["repo"], {"path": str(self.repo), "base": self.base, "branch": branch})
        self.assertEqual(git("rev-parse", f"{branch}~1", cwd=self.repo), self.base)
        self.assertEqual(git("rev-parse", branch, cwd=self.repo), meta["build"]["commit"])
        self.assertEqual(git("diff", "--name-only", self.base, branch, cwd=self.repo).split(),
                         ["__init__.py", "test_generated.py"])  # not the RUN command's byproducts
        self.assertEqual(git("log", "-1", "--format=%an", branch, cwd=self.repo), "Agent Council")
        # The user's own checkout is untouched.
        self.assertEqual((git("rev-parse", "HEAD", cwd=self.repo), git("branch", "--show-current", cwd=self.repo)),
                         (self.base, "main"))
        self.assertEqual(git("status", "--porcelain", cwd=self.repo), "")
        # Agent worktrees are gone; the build worktree stays for inspection.
        self.assertEqual(self.worktree_paths(), [str(self.repo), str(session / "build")])
        final = (session / "final.md").read_text()
        self.assertIn(f"committed on branch **{branch}**", final)
        self.assertIn("Nothing was merged or pushed", final)

    def test_agents_work_in_isolated_worktrees_at_the_base(self):
        proc, session = self.run_council("fail_final", "--type", "question", "--repo", str(self.repo), "Explain")
        self.assertEqual(proc.returncode, 75, proc.stdout + proc.stderr)  # paused: worktrees kept for --resume
        for name in ("alpha", "beta", "gamma"):
            tree = session / "agents" / name
            self.assertEqual((tree / "owner.txt").read_text(), name)  # each agent's own checkout
            self.assertEqual(git("rev-parse", "HEAD", cwd=tree), self.base)
            self.assertTrue((tree / "README.md").exists())
        self.assertIn("REPOSITORY: your working directory is your own git worktree",
                      next((session / "rounds").glob("round1.alpha.prompt.txt")).read_text())
        self.assertFalse((self.repo / "owner.txt").exists())
        self.assertNotIn("council/", git("branch", "--list", cwd=self.repo))  # no build, no branch

    def test_question_session_cleans_up(self):
        proc, session = self.run_council("question", "--type", "question", "--repo", str(self.repo), "Explain")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.worktree_paths(), [str(self.repo)])
        self.assertIn("Repository:", (session / "final.md").read_text())

    def test_rejects_non_repositories(self):
        plain = self.root / "plain"
        plain.mkdir()
        proc, session = self.run_council("question", "--repo", str(plain), "Explain")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not inside a git repository", proc.stderr)
        self.assertIsNone(session)


if __name__ == "__main__":
    unittest.main()
