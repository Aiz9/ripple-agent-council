"""--probe: one short request per installed adapter or configured agent."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

PROJECT = Path(__file__).resolve().parents[1]
CLAUDE = """#!{python}
import json, sys
assert "READY" in sys.stdin.read()
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": "READY"}}))
"""
CODEX = """#!{python}
import sys
print("Error: not logged in. Please run codex login", file=sys.stderr)
sys.exit(1)
"""


class ProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name, body in (("claude", CLAUDE), ("codex", CODEX)):
            path = self.bin / name
            path.write_text(body.format(python=sys.executable))
            path.chmod(0o755)
        self.env = os.environ | {"PATH": f"{self.bin}{os.pathsep}{os.path.dirname(sys.executable)}",
                                 "HOME": str(self.root)}  # no real Claude config to copy

    def council(self, *args):
        return subprocess.run([sys.executable, str(PROJECT / "council.py"), "--sessions-dir", str(self.root / "s"),
                               *args], capture_output=True, text=True, timeout=60, env=self.env)

    def test_adapters_probe(self):
        proc = self.council("--adapters", "--probe")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Probing 2 agent(s)", proc.stdout)
        self.assertRegex(proc.stdout, r"claude: READY \(")
        self.assertIn("codex: FAILED [authentication]", proc.stdout)

    def test_doctor_probe_counts_only_working_agents(self):
        config = self.root / "agents.yaml"
        config.write_text(yaml.safe_dump({"agents": [{"adapter": "claude"}, {"adapter": "codex"}]}))
        proc = self.council("--config", str(config), "--doctor", "--probe")
        self.assertEqual(proc.returncode, 2, proc.stdout)  # one working agent is below min_agents
        self.assertIn("claude: READY", proc.stdout)
        self.assertNotIn("does not test authentication", proc.stdout)

    def test_probe_needs_a_listing(self):
        proc = self.council("--probe")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--probe goes with --adapters or --doctor", proc.stderr)


if __name__ == "__main__":
    unittest.main()
