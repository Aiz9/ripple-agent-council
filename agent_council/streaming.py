"""Live display of agent output while a call runs, and Claude's stream-json envelope.

Only output modes whose standard output carries answer text as it is produced
can stream: plain ``stdout``, Claude Code ``claude_stream_json`` (text deltas
from ``--include-partial-messages``), and OpenCode ``opencode_json`` text parts.
File-based modes write the answer at the end, so they are shown when done.
Streamed text is display only; the recorded answer is always parsed from the
complete output afterwards.
"""
from __future__ import annotations

import json

STREAMABLE = frozenset({"stdout", "claude_stream_json", "opencode_json"})


def claude_envelope(mode: str, stdout: str):
    """The result object of claude_json (one object) or claude_stream_json (JSONL
    ending in a ``result`` event). Raises ValueError when there is none."""
    if mode == "claude_json":
        return json.loads(stdout)
    result = None
    for line in stdout.splitlines():
        if line.strip():
            event = json.loads(line)
            if isinstance(event, dict) and event.get("type") == "result":
                result = event
    if result is None:
        raise ValueError("Claude stream has no result event")
    return result


def delta(mode: str, line: str) -> str | None:
    """Answer text carried by one stdout line, if any."""
    if mode == "stdout":
        return line
    try:
        event = json.loads(line)
    except ValueError:
        return None
    if not isinstance(event, dict):
        return None
    if mode == "claude_stream_json":
        inner = event.get("event") if event.get("type") == "stream_event" else None
        if (isinstance(inner, dict) and inner.get("type") == "content_block_delta"
                and event.get("parent_tool_use_id") is None):  # not a subagent's text
            piece = inner.get("delta") or {}
            if piece.get("type") == "text_delta" and isinstance(piece.get("text"), str):
                return piece["text"]
    elif mode == "opencode_json" and event.get("type") == "text":
        text = (event.get("part") or {}).get("text")
        return text + "\n" if isinstance(text, str) else None
    return None


class LineStreamer:
    """Collect text deltas and emit them as whole, labelled lines."""

    def __init__(self, label: str, mode: str, emit):
        self.label, self.mode, self.emit = label, mode, emit
        self.buffer, self.emitted = "", False

    def feed(self, line: str) -> None:
        text = delta(self.mode, line)
        if not text:
            return
        self.buffer += text
        *complete, self.buffer = self.buffer.split("\n")
        for piece in complete:
            self._emit(piece)

    def close(self) -> None:
        if self.buffer:
            self._emit(self.buffer)
            self.buffer = ""

    def _emit(self, piece: str) -> None:
        self.emitted = True
        self.emit(f"{self.label} │ {piece}")
