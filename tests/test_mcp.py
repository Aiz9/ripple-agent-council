"""The stdio MCP server: protocol handshake, tools, and a real council run."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fake_agent.py").resolve()


class McpServerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        agents = []
        for name, mode in (("alpha", "claude_json"), ("beta", "file"), ("gamma", "stdout")):
            argv = [sys.executable, str(FIXTURE), name, mode, "{prompt}"] + (["{outfile}"] if mode == "file" else [])
            agents.append({"name": name, "output_mode": mode, "argv": argv, "env": {"SCENARIO": "question"}})
        self.config = self.root / "agents.yaml"
        self.config.write_text(yaml.safe_dump({"settings": {"max_rounds": 3, "per_agent_timeout_sec": 5},
                                               "agents": agents}))
        self.server = subprocess.Popen([sys.executable, str(PROJECT / "council.py"), "--config", str(self.config),
                                        "--sessions-dir", str(self.root / "s"), "--mcp"],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop)
        self.next_id = 0

    def stop(self):
        if self.server.poll() is None:
            self.server.stdin.close()
            self.server.wait(timeout=20)
        self.server.stdout.close()
        self.server.stderr.close()

    def send(self, method, params=None, notify=False):
        message = {"jsonrpc": "2.0", "method": method} | ({"params": params} if params is not None else {})
        if not notify:
            self.next_id += 1
            message["id"] = self.next_id
        self.server.stdin.write(json.dumps(message) + "\n")
        self.server.stdin.flush()
        if notify:
            return None
        reply = json.loads(self.server.stdout.readline())  # every stdout line must be protocol JSON
        self.assertEqual(reply["id"], self.next_id)
        return reply

    def call(self, name, arguments):
        return self.send("tools/call", {"name": name, "arguments": arguments})["result"]

    def test_handshake_and_tools(self):
        reply = self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "test", "version": "0"}})
        self.assertEqual(reply["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", reply["result"]["capabilities"])
        self.send("notifications/initialized", notify=True)
        self.assertEqual(self.send("ping")["result"], {})
        names = [tool["name"] for tool in self.send("tools/list")["result"]["tools"]]
        self.assertEqual(names, ["ask_council", "list_sessions", "get_session"])
        self.assertEqual(self.send("nope")["error"]["code"], -32601)
        self.assertEqual(self.send("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"],
                         "2025-06-18")

    def test_ask_list_and_get(self):
        self.send("initialize", {"protocolVersion": "2025-03-26"})
        result = self.call("ask_council", {"prompt": "--help is not an option. Which database?",
                                           "agents": "alpha,beta", "max_rounds": 3})
        self.assertFalse(result["isError"], result)
        data = result["structuredContent"]
        self.assertEqual((data["status"], data["exit_code"], data["agents"]), ("completed", 0, ["alpha", "beta"]))
        self.assertIn("Final answer includes original evidence", result["content"][0]["text"])
        self.assertEqual(data["question"], "--help is not an option. Which database?")
        listed = self.call("list_sessions", {})["structuredContent"]["sessions"]
        self.assertEqual(listed[0]["session"], data["session"])
        got = self.call("get_session", {"session": data["session"]})
        self.assertEqual(got["structuredContent"]["answer"], data["answer"])
        self.assertNotIn("claude-config", json.dumps(got))

    def test_tool_errors(self):
        for name, arguments in (("ask_council", {"prompt": " "}), ("get_session", {"session": "../etc"}),
                                ("get_session", {"session": "missing"}), ("list_sessions", {"limit": 0}),
                                ("ask_council", {"prompt": "Q", "agents": "nobody,else"})):
            with self.subTest(name=name, arguments=arguments):
                self.assertTrue(self.call(name, arguments)["isError"])
        self.assertEqual(self.send("tools/call", {"name": "rm", "arguments": {}})["error"]["code"], -32603)


if __name__ == "__main__":
    unittest.main()
