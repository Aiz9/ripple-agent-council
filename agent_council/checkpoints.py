"""Durable call checkpoints and exclusive ownership of resumable sessions.

Nothing in this module launches an agent. A resume caller must hold SessionLock
while recovering tracked children, replaying cached calls, and updating state.
"""
from __future__ import annotations

from copy import deepcopy
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any
import uuid

try:
    import fcntl
except ImportError:  # pragma: no cover - resume requires POSIX locking
    fcntl = None


def atomic_json(path: Path, data: Any) -> None:
    """Replace a JSON file durably, using an owner-private temporary file.

    Flush the file before rename and its parent directory afterwards. A crash
    exposes either the previous complete document or the new complete document.
    """
    path = Path(path)
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class SessionLock:
    """Nonblocking process lock, released by the kernel even after SIGKILL.

    The lock file is deliberately retained: unlinking it would let a new caller
    acquire a different inode while another caller still owns the original.
    """

    def __init__(self, session: Path):
        self.session = Path(session)
        self._fd: int | None = None

    def __enter__(self) -> SessionLock:
        if fcntl is None:
            raise ValueError("Resuming sessions requires POSIX file locking")
        if self._fd is not None:
            raise RuntimeError("SessionLock is already held")
        if not self.session.is_dir():
            raise ValueError(f"Session directory does not exist: {self.session}")
        fd = os.open(self.session / "session.lock",
                     os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    raise ValueError(f"Session is already in use: {self.session}") from exc
                raise
            os.fchmod(fd, 0o600)
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _object(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _complete(session: Path, run: dict) -> bool:
    meta = _object(session / "session.json") or {}
    return (meta.get("status") == "completed" or run.get("status") == "completed"
            or run.get("completed") is True)


def resolve_session(spec: str, sessions_root: Path) -> Path:
    """Find an existing checkpoint-enabled session without creating anything.

    ``latest`` selects the newest run.json among incomplete sessions. Explicit
    paths and directory IDs may name a completed session so callers can report
    its saved outcome without starting additional work.
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("A session path, ID, or 'latest' is required")
    spec = spec.strip()
    sessions_root = Path(sessions_root).expanduser().resolve()
    if spec == "latest":
        candidates = []
        try:
            children = list(sessions_root.iterdir())
        except OSError as exc:
            raise ValueError(f"No incomplete checkpoint-enabled sessions in {sessions_root}") from exc
        for session in children:
            if not session.is_dir():
                continue
            run = _object(session / "run.json")
            if run is None or _complete(session, run):
                continue
            try:
                modified = (session / "run.json").stat().st_mtime_ns
            except OSError:
                continue
            candidates.append((modified, session.name, session))
        if not candidates:
            raise ValueError(f"No incomplete checkpoint-enabled sessions in {sessions_root}")
        return max(candidates)[2].resolve()

    candidate = Path(spec).expanduser()
    if not candidate.exists():
        # A bare ID is resolved under the state root. Missing paths containing
        # separators stay missing instead of silently selecting another run.
        if candidate.name != spec or spec in (".", ".."):
            raise ValueError(f"Session directory does not exist: {candidate}")
        candidate = sessions_root / spec
    if not candidate.is_dir():
        raise ValueError(f"Session directory does not exist: {candidate}")
    if _object(candidate / "run.json") is None:
        raise ValueError(f"Session has no valid run.json checkpoint: {candidate}")
    return candidate.resolve()


def _call_key(tag: str, name: str) -> str:
    for label, value in (("tag", tag), ("agent name", name)):
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", value):
            raise ValueError(f"Invalid checkpoint {label}: {value!r}")
    # The digest disambiguates pairs such as (a.b, c) and (a, b.c). Truncated
    # readable labels keep each filename below typical NAME_MAX limits.
    digest = hashlib.sha256(f"{tag}\0{name}".encode("utf-8")).hexdigest()
    return f"{tag[:48]}.{name[:48]}.{digest}"


class CallStore:
    """Cache complete per-agent results and retain immutable attempt records."""

    def __init__(self, session: Path):
        self.session = Path(session)

    def load(self, tag: str, name: str, prompt: str, *, include_failure: bool = False) -> dict | None:
        entry = _object(self.session / "calls" / f"{_call_key(tag, name)}.json")
        if (entry is None or entry.get("schema_version") != 1 or entry.get("tag") != tag
                or entry.get("name") != name
                or entry.get("prompt_sha256") != hashlib.sha256(prompt.encode("utf-8")).hexdigest()):
            return None
        result = entry.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            return None
        if result["ok"] is not True and not include_failure:
            return None
        return deepcopy(result)

    def save(self, tag: str, name: str, prompt: str, result: dict) -> None:
        key = _call_key(tag, name)
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            raise ValueError("A checkpoint result must be a mapping with a boolean 'ok' field")
        entry = {"schema_version": 1, "tag": tag, "name": name,
                 "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                 "saved_at_ns": time.time_ns(), "result": deepcopy(result)}
        # Cache first: once a successful call is checkpointed, failure to retain
        # a diagnostic attempt must not force it to run again after a crash.
        atomic_json(self.session / "calls" / f"{key}.json", entry)
        atomic_json(self.session / "attempts" / f"{key}.{uuid.uuid4().hex}.json", entry)


def process_identity(pid: int) -> str | None:
    """Return a Linux process start token, including boot identity when known.

    /proc/stat's command name can contain spaces and parentheses, so split only
    after its final closing parenthesis. Missing identities are never trusted.
    """
    if not sys.platform.startswith("linux") or type(pid) is not int or pid <= 1:
        return None
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = stat.rsplit(")", 1)[1].split()
        start = fields[19]  # Field 22; fields[0] is process state (field 3).
        if not start.isdigit():
            return None
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        if not boot:
            return None
        return f"{boot}:{start}"
    except (OSError, UnicodeError, IndexError):
        return None


def track_process(path: Path, proc: subprocess.Popen) -> dict | None:
    """Record a dedicated child process group before waiting for its output.

    The caller must use ``start_new_session=True`` and remove this marker once
    the command has ended normally. Short-lived children may already be gone.
    """
    if os.name != "posix":
        return None
    identity = process_identity(proc.pid)
    if identity is None:
        return None
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return None
    if pgid != proc.pid or pgid == os.getpgrp():
        raise ValueError("Tracked commands must run in their own process group")
    if process_identity(proc.pid) != identity:
        return None
    marker = {"schema_version": 1, "pid": proc.pid, "pgid": pgid,
              "start_token": identity, "status": "running", "tracked_at_ns": time.time_ns()}
    atomic_json(Path(path), marker)
    return marker


def recover_processes(session: Path) -> list[int]:
    """Kill leftover groups whose recorded live leader can still be verified.

    PID reuse, a different boot, malformed markers, and departed group leaders
    all fail closed. Recovery never guesses the identity of an orphaned group.
    Call this only while holding the session lock.
    """
    recovered = []
    if os.name != "posix":
        return recovered
    for path in sorted((Path(session) / "processes").glob("*.json")):
        marker = _object(path)
        if not marker or marker.get("status") != "running":
            continue
        pid, pgid, token = marker.get("pid"), marker.get("pgid"), marker.get("start_token")
        matches = (type(pid) is int and pid > 1 and type(pgid) is int and pgid == pid
                   and pgid != os.getpgrp() and isinstance(token, str) and bool(token))
        if matches:
            try:
                matches = (process_identity(pid) == token and os.getpgid(pid) == pgid
                           and process_identity(pid) == token)
            except ProcessLookupError:
                matches = False
        if matches:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                matches = False
            else:
                recovered.append(pid)
        marker.update(status="recovered" if matches else "stale", recovered_at_ns=time.time_ns())
        atomic_json(path, marker)
    return recovered
