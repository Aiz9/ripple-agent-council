"""Follow-up questions: continue a finished session as a conversation.

A follow-up session's prompt carries every earlier question and council answer in
its chain, oldest first, and its metadata links to its parent. Answers are
included in full (never truncated); long chains therefore produce long prompts,
which stdin-delivery agents handle without the argument-size limit.
"""
from __future__ import annotations

import json
from pathlib import Path

MAX_DEPTH = 50


def _read(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _finished(session: Path) -> bool:
    meta = _read(session / "session.json") or {}
    answer = _read(session / "final-answer.json") or {}
    return meta.get("status") == "completed" and isinstance(answer.get("text"), str)


def find_parent(spec: str, root: Path) -> Path:
    """Resolve 'latest', an ID, or a path to a completed session with a final answer."""
    root = Path(root).expanduser().resolve()
    if spec == "latest":
        candidates = [s for s in (root.iterdir() if root.is_dir() else []) if s.is_dir() and _finished(s)]
        if not candidates:
            raise ValueError(f"No completed session to continue in {root}")
        return max(candidates, key=lambda s: ((s / "final-answer.json").stat().st_mtime_ns, s.name)).resolve()
    candidate = Path(spec).expanduser()
    if not candidate.exists() and candidate.name == spec:
        candidate = root / spec
    if not candidate.is_dir():
        raise ValueError(f"Session directory does not exist: {candidate}")
    if not _finished(candidate):
        raise ValueError(f"Session {candidate.name} has no completed answer to continue from")
    return candidate.resolve()


def chain(session: Path) -> list[dict]:
    """The question/answer history ending at `session`, oldest first."""
    entries, seen, current = [], set(), Path(session)
    while current is not None and len(entries) < MAX_DEPTH and current not in seen:
        seen.add(current)
        meta, answer = _read(current / "session.json") or {}, _read(current / "final-answer.json") or {}
        entries.append({"session": current.name, "question": meta.get("question") or meta.get("goal", ""),
                        "answer": answer.get("text", "")})
        parent = meta.get("parent_path")
        current = Path(parent) if isinstance(parent, str) and Path(parent).is_dir() else None
    return list(reversed(entries))


def compose(history: list[dict], question: str) -> str:
    turns = "\n\n".join(f"--- Earlier council session {entry['session']} ---\nQuestion:\n{entry['question']}\n\n"
                        f"Council answer:\n{entry['answer']}" for entry in history)
    return ("This is a follow-up in a continuing conversation. Use the earlier answers as context, "
            "correct them if new evidence shows they were wrong, and answer the new question.\n\n"
            f"PRIOR CONVERSATION (oldest first):\n{turns}\n\nNEW QUESTION:\n{question}")
