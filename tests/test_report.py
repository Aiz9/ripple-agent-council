"""HTML session reports and the local session viewer."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import yaml

from agent_council.report import build_server, render_session

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fake_agent.py").resolve()


class ReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()  # git and the CLI report resolved paths

    def real_session(self):
        agents = []
        for name, mode in (("alpha", "claude_json"), ("beta", "file"), ("gamma", "stdout")):
            argv = [sys.executable, str(FIXTURE), name, mode, "{prompt}"] + (["{outfile}"] if mode == "file" else [])
            agents.append({"name": name, "output_mode": mode, "argv": argv, "env": {"SCENARIO": "question"}})
        config = self.root / "agents.yaml"
        config.write_text(yaml.safe_dump({"settings": {"max_rounds": 3, "per_agent_timeout_sec": 5}, "agents": agents,
                                          "guardrails": [{"rule": "forbid", "pattern": "CHALLENGE:", "action": "flag"}]}))
        proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--config", str(config), "--sessions-dir",
                               str(self.root / "s"), "--non-interactive", "--quiet", "--anonymous-review",
                               "Which database?"], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return next((self.root / "s").iterdir())

    def test_report_of_a_real_session(self):
        session = self.real_session()
        proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--sessions-dir", str(self.root / "s"),
                               "--report"], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(proc.stdout.strip()), session / "report.html")
        page = (session / "report.html").read_text()
        for expected in ("Which database?", "Timeline", "agent calls over",
                         "Anonymous peer ranking", "Guardrail", "<mark>CHALLENGE: retain this claim.</mark>",
                         "Final answer includes original evidence", "round0", "ranking", "final"):
            self.assertIn(expected, page)
        self.assertNotIn("claude-config", page)

    def test_everything_is_escaped(self):
        session = self.root / "evil"
        (session / "rounds").mkdir(parents=True)
        (session / "session.json").write_text(json.dumps({"question": "<script>alert(1)</script>", "status": "completed",
                                                          "roles": {"x<b>": {"name": "r", "description": "d"}}}))
        (session / "rounds/round1.alpha.md").write_text("# round1 — alpha\n\n<img src=x onerror=alert(1)>")
        page = render_session(session)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page)
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<img src=x", page)
        self.assertNotIn("x<b>", page)

    def test_viewer_serves_only_known_sessions(self):
        session = self.real_session()
        server = build_server(self.root / "s", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def get(path):
            try:
                with urllib.request.urlopen(base + path, timeout=10) as response:
                    return response.status, response.read().decode()
            except urllib.error.HTTPError as error:
                error.close()
                return error.code, ""

        status, index = get("/")
        self.assertEqual(status, 200)
        self.assertIn(session.name, index)
        status, page = get(f"/session/{session.name}")
        self.assertEqual(status, 200)
        self.assertIn("Which database?", page)
        for path in ("/session/..", "/session/%2e%2e", f"/session/{session.name}/claude-config",
                     "/session/../etc/passwd", "/etc/passwd", "/session/nope"):
            with self.subTest(path=path):
                self.assertEqual(get(path)[0], 404)
        self.assertEqual(server.server_address[0], "127.0.0.1")

    def test_running_sessions_refresh(self):
        session = self.root / "live"
        session.mkdir()
        (session / "session.json").write_text(json.dumps({"status": "running", "question": "Q"}))
        self.assertIn("http-equiv='refresh'", render_session(session, refresh=True))
        self.assertNotIn("http-equiv='refresh'", render_session(session, refresh=False))


if __name__ == "__main__":
    unittest.main()
