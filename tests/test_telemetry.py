"""Session traces: the local trace.jsonl and OpenTelemetry export."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import yaml

from agent_council import cli
from agent_council.telemetry import read_trace

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fake_agent.py").resolve()

try:  # The SDK is a dev dependency; the OpenTelemetry test skips without it.
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
except ImportError:  # pragma: no cover
    otel_trace = None


def fixture_config(root: Path, **settings) -> Path:
    agents = []
    for name, mode in (("alpha", "claude_json"), ("beta", "file"), ("gamma", "stdout")):
        argv = [sys.executable, str(FIXTURE), name, mode, "{prompt}"] + (["{outfile}"] if mode == "file" else [])
        agents.append({"name": name, "output_mode": mode, "argv": argv, "env": {"SCENARIO": "question"}})
    path = root / "agents.yaml"
    path.write_text(yaml.safe_dump({"settings": {"max_rounds": 3, "per_agent_timeout_sec": 5, **settings},
                                    "agents": agents}))
    return path


class TraceFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_council(self, *args):
        return subprocess.run([sys.executable, str(PROJECT / "council.py"), "--sessions-dir", str(self.root / "s"),
                               "--quiet", *args], capture_output=True, text=True, timeout=30)

    def test_spans_nest_session_phase_call(self):
        proc = self.run_council("--config", str(fixture_config(self.root)), "--non-interactive", "Explain")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        spans = read_trace(next((self.root / "s").iterdir()) / "trace.jsonl")
        by_id = {s["span_id"]: s for s in spans}
        root = next(s for s in spans if s["name"] == "council session")
        self.assertEqual((root["parent_id"], root["status"], root["attributes"]["exit_code"]), (None, "ok", 0))
        phases = [s for s in spans if s["parent_id"] == root["span_id"]]
        self.assertEqual([s["name"] for s in sorted(phases, key=lambda s: s["start"])],
                         ["framing", "round1", "round2", "round3", "synthesis"])
        calls = [s for s in spans if s["name"] == "agent call"]
        self.assertEqual(len(calls), 11)
        for call in calls:
            self.assertEqual(by_id[call["parent_id"]]["parent_id"], root["span_id"])
        alpha = next(c for c in calls if c["attributes"]["agent"] == "alpha")
        self.assertEqual((alpha["attributes"]["tokens"], alpha["attributes"]["cost_usd"]), (150, 0.25))
        self.assertTrue(all(s["trace_id"] == root["trace_id"] for s in spans))

    def test_resumed_session_marks_reused_calls(self):
        paused = self.run_council("--config", str(fixture_config(self.root)), "--non-interactive",
                                  "--max-cost", "0.5", "Explain")
        self.assertEqual(paused.returncode, 75, paused.stdout + paused.stderr)
        session = next((self.root / "s").iterdir())
        resumed = self.run_council("--resume", str(session), "--max-cost", "10")
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        spans = read_trace(session / "trace.jsonl")
        roots = [s for s in spans if s["name"] == "council session"]
        self.assertEqual([r["status"] for r in roots], ["paused", "ok"])
        self.assertEqual(len({r["trace_id"] for r in roots}), 2)
        self.assertTrue(any(s["name"] == "agent call" and s["status"] == "budget" for s in spans))
        reused = [s for s in spans if s["name"] == "agent call" and s["attributes"].get("reused")]
        self.assertGreaterEqual(len(reused), 4)

    def test_unreadable_lines_are_skipped(self):
        path = self.root / "trace.jsonl"
        path.write_text('{"name": "ok"}\n{"name": "trunc')
        self.assertEqual(read_trace(path), [{"name": "ok"}])


@unittest.skipIf(otel_trace is None, "opentelemetry-sdk not installed")
class OpenTelemetryTests(unittest.TestCase):
    exporter = None

    @classmethod
    def setUpClass(cls):
        # An application-provided provider must be used as-is (set once per process).
        cls.exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(cls.exporter))
        otel_trace.set_tracer_provider(provider)

    def test_spans_reach_opentelemetry_with_parents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = cli.main(["--config", str(fixture_config(root)), "--sessions-dir", str(root / "s"),
                                 "--non-interactive", "--quiet", "Explain"])
        self.assertEqual(code, 0)
        spans = self.exporter.get_finished_spans()
        names = [s.name for s in spans]
        self.assertIn("council session", names)
        self.assertEqual(names.count("agent call"), 11)
        session = next(s for s in spans if s.name == "council session")
        framing = next(s for s in spans if s.name == "framing")
        self.assertEqual(framing.parent.span_id, session.context.span_id)
        call = next(s for s in spans if s.name == "agent call")
        self.assertIn(call.parent.span_id, {s.context.span_id for s in spans if s.parent
                                            and s.parent.span_id == session.context.span_id})
        self.assertEqual(call.attributes["council.reused"], False)
        self.assertEqual(session.attributes["council.exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
