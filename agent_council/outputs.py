"""Validate structured CLI envelopes before treating their text as an answer.

Gemini emits one JSON object, Qwen Code a JSON array ending in a result,
OpenCode JSONL text/step events, and Cursor one result object. Process exit
status is checked by the caller; these parsers additionally reject errors and
incomplete envelopes.
"""

import json


OUTPUT_MODES = frozenset({"gemini_json", "qwen_json", "opencode_json", "cursor_json"})


def _object(value: object, context: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{context}: expected a JSON object")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: expected a nonempty string")
    return value


def _gemini(stdout: str) -> str:
    obj = _object(json.loads(stdout), "Gemini output")
    if "error" in obj:
        raise ValueError("Gemini returned an error envelope")
    return _string(obj.get("response"), "Gemini response").strip()


def _qwen(stdout: str) -> str:
    events = json.loads(stdout)
    if not isinstance(events, list) or not events:
        raise ValueError("Qwen output: expected a nonempty JSON array")
    for event in events:
        obj = _object(event, "Qwen event")
        _string(obj.get("type"), "Qwen event type")
        if obj["type"] == "error" or obj.get("is_error") is True:
            raise ValueError("Qwen returned an error event")
        if obj["type"] == "result" and obj is not events[-1]:
            raise ValueError("Qwen result must be the terminal event")
    result = events[-1]
    if result["type"] != "result":
        raise ValueError("Qwen output has no terminal result")
    if result.get("is_error") is not False or result.get("subtype") != "success":
        raise ValueError("Qwen result is not successful")
    if "error" in result:
        raise ValueError("Qwen result contains an error")
    return _string(result.get("result"), "Qwen result").strip()


def _opencode(stdout: str) -> str:
    # OpenCode emits no standalone result. A final step_finish(reason='stop')
    # identifies the completed assistant message; earlier tool steps may also
    # contain prose and must never supply the answer for that final message.
    texts: dict[str, list[str]] = {}
    current: str | None = None
    completed: str | None = None
    for line_number, line in enumerate(stdout.splitlines(), 1):
        if not line.strip():
            continue
        obj = _object(json.loads(line), f"OpenCode event on line {line_number}")
        kind = _string(obj.get("type"), "OpenCode event type")
        if kind == "error":
            raise ValueError("OpenCode returned an error event")
        if kind not in {"text", "step_start", "step_finish", "tool_use", "reasoning"}:
            continue
        part = _object(obj.get("part"), f"OpenCode {kind} part")
        message = _string(part.get("messageID"), "OpenCode messageID")
        if current != message or kind == "step_start":
            current = message
            completed = None
        if kind == "step_start":
            texts.pop(message, None)
        if kind == "text":
            text = part.get("text")
            if not isinstance(text, str):
                raise ValueError("OpenCode text must be a string")
            texts.setdefault(message, []).append(text)
        elif kind == "step_finish":
            reason = _string(part.get("reason"), "OpenCode finish reason")
            completed = message if reason == "stop" else None
            if reason != "stop":
                texts.pop(message, None)
        elif kind == "tool_use":
            # Any tool work after a stop requires a subsequent final step.
            completed = None
    if completed is None:
        raise ValueError("OpenCode output has no completed final message")
    return _string("".join(texts.get(completed, [])), "OpenCode final response").strip()


def _cursor(stdout: str) -> str:
    # Success is one result object; Cursor emits no JSON at all when it fails.
    obj = _object(json.loads(stdout), "Cursor output")
    if obj.get("type") != "result" or obj.get("subtype") != "success" or obj.get("is_error") is not False:
        raise ValueError("Cursor result is not successful")
    return _string(obj.get("result"), "Cursor result").strip()


def parse_structured(mode: str, stdout: str) -> str:
    """Return only a completed answer, raising ValueError for invalid output."""
    if not isinstance(stdout, str):
        raise ValueError("CLI output must be text")
    parsers = {"gemini_json": _gemini, "qwen_json": _qwen, "opencode_json": _opencode,
               "cursor_json": _cursor}
    if mode not in parsers:
        raise ValueError(f"Unknown structured output mode: {mode!r}")
    return parsers[mode](stdout)
