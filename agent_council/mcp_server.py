"""Council as an MCP server (``council --mcp``): stdio JSON-RPC 2.0, one message per line.

Tools: ``ask_council`` runs a full non-interactive council session in a child
process and returns its final answer; ``list_sessions`` and ``get_session`` read
saved sessions. Only summaries and final answers leave the server; transcripts,
prompts, and copied agent configuration stay on disk. Standard output carries
protocol messages only; diagnostics go to standard error.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import threading

from . import __version__

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
SESSION_ID = re.compile(r"[A-Za-z0-9_.-]+")
TOOLS = [
    {"name": "ask_council",
     "description": "Ask a council of AI coding agents (Claude Code, Codex, Gemini, ...) to debate a question or "
                    "build request and return their verified, synthesized answer. Slow: several minutes.",
     "inputSchema": {"type": "object", "required": ["prompt"], "properties": {
         "prompt": {"type": "string", "description": "The question or build request."},
         "agents": {"type": "string", "description": "Comma-separated agents or adapters, or auto[:N]."},
         "preset": {"type": "string", "description": "A preset such as quick, deep, blind, hierarchy."},
         "type": {"type": "string", "enum": ["auto", "question", "build"]},
         "max_rounds": {"type": "integer", "minimum": 1},
         "max_cost_usd": {"type": "number", "exclusiveMinimum": 0},
         "anonymous_review": {"type": "boolean"},
         "continue_from": {"type": "string", "description": "Session ID to ask a follow-up to."}}}},
    {"name": "list_sessions", "description": "List recent council sessions with status and question.",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}}},
    {"name": "get_session", "description": "Get a session's status, consensus, usage, and final answer.",
     "inputSchema": {"type": "object", "required": ["session"],
                     "properties": {"session": {"type": "string", "description": "Session ID."}}}},
]


class ToolError(Exception):
    pass


class Server:
    def __init__(self, sessions: Path, config: Path | None = None, stdout=None, *, skill_options: list[str] | None = None):
        self.sessions, self.config = Path(sessions), config
        self.skill_options = list(skill_options or [])
        self.out = stdout or sys.stdout
        self.lock = threading.Lock()
        self.children: set[subprocess.Popen] = set()

    def send(self, message: dict) -> None:
        with self.lock:
            self.out.write(json.dumps(message) + "\n")
            self.out.flush()

    def serve(self, stdin=None) -> None:
        workers = []
        for line in stdin or sys.stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                continue
            if isinstance(message, dict) and message.get("method") == "tools/call" and "id" in message:
                worker = threading.Thread(target=self.handle, args=(message,), daemon=True)
                worker.start()  # council runs are slow; keep answering pings meanwhile
                workers.append(worker)
            else:
                self.handle(message)
        for child in list(self.children):  # client went away
            child.terminate()
        for worker in workers:
            worker.join(timeout=10)

    def handle(self, message) -> None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
            self.send({"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                       "error": {"code": -32600, "message": "invalid request"}})
            return
        if "id" not in message:
            return  # notifications (initialized, cancelled) need no reply
        method, params, ident = message["method"], message.get("params") or {}, message["id"]
        try:
            if method == "initialize":
                asked = params.get("protocolVersion")
                result = {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "agent-council", "version": __version__},
                          "instructions": "Use ask_council for questions that benefit from several independent "
                                          "agents cross-examining each other; it takes minutes."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self.call_tool(params.get("name"), params.get("arguments") or {})
            else:
                self.send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"unknown method {method}"}})
                return
        except Exception as exc:  # never let one request kill the server
            self.send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32603, "message": str(exc)}})
            return
        self.send({"jsonrpc": "2.0", "id": ident, "result": result})

    def call_tool(self, name, arguments: dict) -> dict:
        tools = {"ask_council": self.ask_council, "list_sessions": self.list_sessions, "get_session": self.get_session}
        if name not in tools:
            raise ValueError(f"unknown tool {name!r}")
        try:
            text, data = tools[name](arguments)
        except ToolError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        return {"content": [{"type": "text", "text": text}], "structuredContent": data, "isError": False}

    def ask_council(self, arguments: dict):
        prompt = arguments.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ToolError("prompt must be nonempty text")
        command = [sys.executable, "-m", "agent_council", "--non-interactive", "--quiet",
                   "--sessions-dir", str(self.sessions)]
        if self.config:
            command += ["--config", str(self.config)]
        command += self.skill_options
        options = {"agents": "--agents", "preset": "--preset", "type": "--type", "max_rounds": "--max-rounds",
                   "max_cost_usd": "--max-cost", "continue_from": "--continue-from"}
        for key, flag in options.items():
            if arguments.get(key) not in (None, ""):
                command += [flag, str(arguments[key])]
        if arguments.get("anonymous_review"):
            command.append("--anonymous-review")
        command.append("-")  # prompt via stdin: no argument-size limit, no option injection
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, encoding="utf-8", errors="replace")
        self.children.add(child)
        try:
            stdout, stderr = child.communicate(prompt)
        finally:
            self.children.discard(child)
        found = re.findall(r"(?m)^\[council\] Session: (.+)$", stdout)
        if not found:
            raise ToolError(f"council did not start (exit {child.returncode}): {(stderr or stdout).strip()[-1500:]}")
        text, data = self.describe(Path(found[-1].strip()))
        data["exit_code"] = child.returncode
        return text, data

    def list_sessions(self, arguments: dict):
        limit = arguments.get("limit", 20)
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ToolError("limit must be an integer from 1 to 200")
        rows = []
        for session in sorted(self.sessions.iterdir() if self.sessions.is_dir() else [], reverse=True):
            meta = self.meta(session)
            if meta is not None:
                rows.append({"session": session.name, "status": meta.get("status"),
                             "question": (meta.get("question") or meta.get("goal") or "")[:200]})
            if len(rows) == limit:
                break
        text = "\n".join(f"{r['session']}  {r['status']}  {r['question']}" for r in rows) or "No sessions."
        return text, {"sessions": rows}

    def get_session(self, arguments: dict):
        spec = arguments.get("session")
        if not isinstance(spec, str) or not SESSION_ID.fullmatch(spec) or spec in (".", ".."):
            raise ToolError("session must be a session ID from list_sessions")
        session = self.sessions / spec
        if self.meta(session) is None:
            raise ToolError(f"no session {spec}")
        return self.describe(session)

    @staticmethod
    def meta(session: Path):
        try:
            data = json.loads((session / "session.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def describe(self, session: Path):
        meta = self.meta(session) or {}
        try:
            final = json.loads((session / "final-answer.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            final = {}
        data = {"session": session.name, "status": meta.get("status"), "consensus": meta.get("consensus"),
                "agents": meta.get("agents"), "question": meta.get("question") or meta.get("goal"),
                "build": (meta.get("build") or {}).get("status"), "usage": meta.get("usage"),
                "pause_reason": meta.get("pause_reason"), "answer": final.get("text")}
        lines = [f"Council session {session.name}: {data['status']}"
                 + (f" (consensus: {data['consensus']})" if data["consensus"] is not None else "")]
        if data["pause_reason"]:
            lines.append(f"Paused: {data['pause_reason']}. Resume with: council --resume {session.name}")
        lines.append("")
        lines.append(data["answer"] or "(no final answer)")
        return "\n".join(lines), data


def serve(sessions: Path, config: Path | None, *, skill_options: list[str] | None = None) -> int:
    print(f"agent-council MCP server {__version__} on stdio; sessions: {sessions}", file=sys.stderr, flush=True)
    Server(sessions, config, skill_options=skill_options).serve()
    return 0
