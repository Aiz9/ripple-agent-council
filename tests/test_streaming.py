"""Live streaming of agent output and the Claude stream-json envelope."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import yaml

from agent_council import cli
from agent_council.streaming import LineStreamer, claude_envelope, delta
from agent_council.usage import parse_usage

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fake_agent.py").resolve()


class StreamingUnitTests(unittest.TestCase):
    def test_deltas(self):
        event = {"type": "stream_event", "parent_tool_use_id": None,
                 "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}}
        self.assertEqual(delta("claude_stream_json", json.dumps(event)), "hi")
        self.assertIsNone(delta("claude_stream_json", json.dumps(event | {"parent_tool_use_id": "t"})))
        self.assertIsNone(delta("claude_stream_json", "not json"))
        self.assertEqual(delta("opencode_json", json.dumps({"type": "text", "part": {"text": "x"}})), "x\n")
        self.assertEqual(delta("stdout", "plain\n"), "plain\n")

    def test_streamer_emits_whole_lines(self):
        lines = []
        streamer = LineStreamer("r1 · a", "stdout", lines.append)
        for piece in ("one ", "two\nthree", "\nfour"):
            streamer.feed(piece)
        self.assertEqual(lines, ["r1 · a │ one two", "r1 · a │ three"])
        streamer.close()
        self.assertEqual(lines[-1], "r1 · a │ four")

    def test_stream_envelope_answer_cost_and_usage(self):
        stdout = "\n".join(json.dumps(e) for e in (
            {"type": "system"}, {"type": "result", "is_error": False, "result": " done ", "total_cost_usd": 0.5,
                                 "usage": {"input_tokens": 3, "output_tokens": 4}}))
        agent = {"output_mode": "claude_stream_json"}
        self.assertEqual(cli.parse_output(agent, stdout, None), "done")
        self.assertEqual(cli.extract_cost(agent, stdout), 0.5)
        self.assertEqual(parse_usage(agent, stdout, "")["total_tokens"], 7)
        with self.assertRaises(ValueError):
            claude_envelope("claude_stream_json", json.dumps({"type": "system"}))
        with self.assertRaises(ValueError):
            cli.parse_output(agent, json.dumps({"type": "result", "is_error": True, "result": "x"}), None)

    def test_lines_arrive_before_the_process_exits(self):
        seen = []
        script = "import sys,time; print('early', flush=True); time.sleep(1); print(sys.stdin.read())"
        with tempfile.TemporaryDirectory() as directory:
            started = time.monotonic()
            proc = cli.run_process([sys.executable, "-c", script], cwd=Path(directory), timeout=10,
                                   input_text="x" * 300_000, on_stdout=lambda line: seen.append(
                                       (line, time.monotonic() - started)))
        self.assertEqual(proc["returncode"], 0)
        self.assertEqual(seen[0][0], "early\n")
        self.assertLess(seen[0][1], proc["seconds"] - 0.5)
        self.assertEqual(proc["stdout"], "early\n" + "x" * 300_000 + "\n")

    def test_timeout_while_streaming(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = cli.run_process([sys.executable, "-c", "import time; print('a', flush=True); time.sleep(30)"],
                                   cwd=Path(directory), timeout=1, on_stdout=lambda line: None)
        self.assertEqual((proc["error"], proc["stdout"]), ("timed out after 1s", "a\n"))


    @unittest.skipUnless(os.name == "posix", "process groups")
    def test_leftover_grandchild_holding_the_pipes_is_ended(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "survived"
            child = f"import time; time.sleep(9); open({str(marker)!r}, 'w').close()"
            parent = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {child!r}]); print('parent done')"
            started = time.monotonic()
            proc = cli.run_process([sys.executable, "-c", parent], cwd=Path(directory), timeout=30)
            self.assertLess(time.monotonic() - started, 8.5)
            self.assertEqual((proc["returncode"], proc["stdout"]), (0, "parent done\n"))
            time.sleep(9.5 - (time.monotonic() - started))
            self.assertFalse(marker.exists())


class StreamingEndToEndTests(unittest.TestCase):
    def run_council(self, *args):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agents = []
            for name, mode in (("alpha", "claude_stream_json"), ("beta", "file"), ("gamma", "stdout")):
                argv = [sys.executable, str(FIXTURE), name, mode, "{prompt}"] + (["{outfile}"] if mode == "file" else [])
                agents.append({"name": name, "output_mode": mode, "argv": argv, "env": {"SCENARIO": "question"}})
            config = root / "agents.yaml"
            config.write_text(yaml.safe_dump({"settings": {"max_rounds": 3, "per_agent_timeout_sec": 5},
                                              "agents": agents}))
            proc = subprocess.run([sys.executable, str(PROJECT / "council.py"), "--config", str(config),
                                   "--sessions-dir", str(root / "s"), "--non-interactive", *args, "Which database?"],
                                  capture_output=True, text=True, timeout=60)
            meta = json.loads(next((root / "s").glob("*/session.json")).read_text())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout, meta

    def test_streamed_turns_are_shown_live_once(self):
        stdout, meta = self.run_council()
        self.assertIn("round1 · alpha │ [VERIFIED: fixture] ORIGINAL_EVIDENCE_alpha.", stdout)
        self.assertIn("round1 · gamma │ [VERIFIED: fixture] ORIGINAL_EVIDENCE_gamma.", stdout)
        self.assertNotIn("--- round1 · alpha ---", stdout)   # not repeated after streaming
        self.assertIn("--- round1 · beta ---", stdout)       # file mode: shown when done
        self.assertNotIn("SUBAGENT TEXT", stdout)
        self.assertEqual(meta["usage"]["by_agent"]["alpha"]["cost_usd"], 0.25 * meta["usage"]["by_agent"]["alpha"]["calls"])

    def test_no_stream(self):
        stdout, _ = self.run_council("--no-stream")
        self.assertNotIn(" │ ", stdout)
        self.assertIn("--- round1 · alpha ---", stdout)


if __name__ == "__main__":
    unittest.main()
